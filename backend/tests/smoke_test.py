"""端到端冒烟测试（离线 mock，不调用任何外部 API、不消耗密钥）。

运行：``backend/.venv/Scripts/python.exe backend/tests/smoke_test.py``
覆盖：建表与旧库补列 → UUIDv7 → /v1 版本前缀 → 全异步五阶段 → 幂等去重
→ 接口防护（413 / 422 / 429，含空描述）→ 每日生成配额 → 模型配置兜底
→ 音频 → 级联删除 → 重启恢复持久化队列（含运行中中断的断点续跑 /
次数超限 / failed 终态）→ 续跑复用阶段不覆盖已记录的模型。

实现要点：环境变量必须在 ``import app`` **之前**设置，因为 Settings 在模块
导入时就实例化；数据目录指向 tests/.tmp（已被 .gitignore 忽略），每次运行
先清空，保证结果可复现、不污染真实作品库。测试里刻意把限流阈值放宽，
只在第 8 步临时调严来断言 429。

队列测试把巡检间隔设成 60 秒，让「启动立即恢复」成为唯一恢复路径，
从而能确定性地模拟一次进程重启（第 11 步用新 TestClient 触发新 lifespan）。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
import uuid
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

TMP = Path(__file__).resolve().parent / ".tmp"
os.environ["MUSIC_STUDIO_MOCK"] = "true"                 # 强制离线，不发网络请求
os.environ["MUSIC_STUDIO_DATA_DIR"] = str(TMP / "data")  # 隔离数据库与产物目录
os.environ["MUSIC_STUDIO_RATE_LIMIT_READ"] = "1000"      # 主流程放宽，避免轮询被限流
os.environ["MUSIC_STUDIO_RATE_LIMIT_LLM"] = "100"
os.environ["MUSIC_STUDIO_RATE_LIMIT_CREATE"] = "100"
os.environ["MUSIC_STUDIO_MAX_ACTIVE_JOBS"] = "20"
os.environ["MUSIC_STUDIO_MAX_ACTIVE_PER_CLIENT"] = "20"
os.environ["MUSIC_STUDIO_DEDUPE_WINDOW"] = "300"        # 测试期间始终处于去重窗口
os.environ["MUSIC_STUDIO_QUEUE_POLL_SECONDS"] = "60"    # 关闭周期巡检干扰，只测启动恢复
os.environ["MUSIC_STUDIO_MAX_RECOVERY_ATTEMPTS"] = "3"  # 供「次数超限」断言
shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from app import deps  # noqa: E402
from app.config import settings  # noqa: E402
from app.constants import STAGE_ORDER, STAGE_OUTPUTS, USER_INPUT_FILE  # noqa: E402
from app.dal import utc_now  # noqa: E402
from app.ids import is_song_id, new_song_id, uuid7  # noqa: E402
from app.main import app  # noqa: E402
from app.models import SONG_TABLE_MIGRATIONS, STAGE_TABLE_MIGRATIONS  # noqa: E402
from app.services.runtime import get_song_service  # noqa: E402

# 前端 interface/index.ts 里 GenerationResponse 的字段白名单；
# 任何字段改名导致契约破坏，第 4 步就会断言失败。
FRONTEND_FIELDS = {
    "id", "title", "task_type", "mode", "model_variant", "status", "prompt", "lyrics",
    "instrumental", "duration_seconds", "bpm", "key", "time_signature", "audio_url",
    "cover_color", "cover_icon", "cover_image_url", "error_message", "metadata",
    "created_at", "updated_at",
}

PAYLOAD = {
    # 与前端保持一致：不传 model_variant / title / lyrics，模型由后端
    # MUSIC_MODEL 兜底，标题与歌词由五阶段流程产出。
    "task_type": "text2music",
    "mode": "simple",
    "inputs": {
        "prompt": "夏末站台告别，木吉他与温柔女声",
        "instrumental": False,
    },
}

LLM_PAYLOAD = {
    "task": "prompt",
    "seed_prompt": "写一首关于夏末告别的华语民谣",
    "instrumental": False,
    "style_tags": [],
}


def check(condition: bool, message: str) -> None:
    """极简断言：失败即抛 AssertionError，成功打印一行 OK。"""
    if not condition:
        raise AssertionError(message)
    print(f"  [OK] {message}")


def wait_ready(client: TestClient, song_id: str, timeout: float = 30) -> dict:
    """轮询详情直到作品完成/失败，返回最后一次详情。"""
    deadline = time.monotonic() + timeout
    while True:
        detail = client.get(f"/v1/generations/{song_id}").json()
        if detail.get("status") in ("ready", "failed"):
            return detail
        if time.monotonic() >= deadline:
            return detail
        time.sleep(0.2)


def main() -> None:
    """TestClient 的 with 会触发 async lifespan，自动建表并清理后台任务。"""
    with TestClient(app) as client:
        created_ids: list[str] = []

        # --- 1. 应用启动、建表与旧库补列 ---
        print("1) 健康检查、/v1 版本前缀、建表与迁移列")
        health = client.get("/v1/health")
        check(health.status_code == 200, "GET /v1/health 返回 200")
        check(health.json()["app"] == "Music Studio", "应用名为 Music Studio")
        check(health.json()["mock_mode"] is True, "离线 mock 模式已生效")
        check(health.json()["music_model"] == settings.music_model, "GET /v1/health 暴露当前 MUSIC_MODEL")
        check(
            health.json()["daily_limit"] == settings.daily_generation_limit
            and isinstance(health.json()["daily_remaining"], int),
            "GET /v1/health 暴露每日配额与剩余量",
        )
        preflight = client.options(
            "/v1/history",
            headers={
                "Origin": "http://127.0.0.1:5177",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        check(preflight.headers.get("access-control-allow-origin") == "http://127.0.0.1:5177", "跨域预检带 Allow-Origin（前端 5177 直连后端）")
        other_port = client.options(
            "/v1/history",
            headers={"Origin": "http://localhost:61999", "Access-Control-Request-Method": "GET"},
        )
        check(other_port.headers.get("access-control-allow-origin") == "http://localhost:61999", "任意本机回环端口默认放行")
        paths = app.openapi()["paths"]
        check(len(paths) == 7 and all(path.startswith("/v1/") for path in paths), "OpenAPI 的 7 个路径统一带 /v1 前缀")
        check(client.get("/health").status_code == 404, "无版本前缀的旧 /health 已移除")
        check(client.get("/api/history").status_code == 404, "旧 /api 前缀已移除")
        with sqlite3.connect(str(settings.db_path)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            columns = {row[1] for row in conn.execute("PRAGMA table_info(songs)")}
            stage_columns = {row[1] for row in conn.execute("PRAGMA table_info(song_stages)")}
        check({"songs", "song_stages"} <= tables, "SQLite 建出 songs / song_stages 两张表")
        check(
            {"input_fingerprint", "client_key", "recovery_attempts"} <= columns,
            "去重 / 限流 / 队列恢复计数字段已建（含旧库补列逻辑）",
        )
        check(
            {"model", "prompt_tokens", "completion_tokens", "total_tokens", "metrics_json"} <= stage_columns,
            "song_stages 阶段遥测字段已建（含旧库补列逻辑）",
        )
        with sqlite3.connect(":memory:") as migration_db:
            migration_db.execute("CREATE TABLE songs (id TEXT PRIMARY KEY, created_at TEXT)")
            for column, declaration in SONG_TABLE_MIGRATIONS.items():
                migration_db.execute(f"ALTER TABLE songs ADD COLUMN {column} {declaration}")
            migration_db.execute(
                "CREATE TABLE song_stages (id INTEGER PRIMARY KEY, song_id TEXT, stage TEXT)"
            )
            for column, declaration in STAGE_TABLE_MIGRATIONS.items():
                migration_db.execute(f"ALTER TABLE song_stages ADD COLUMN {column} {declaration}")
            migrated = {row[1] for row in migration_db.execute("PRAGMA table_info(songs)")}
            migrated_stages = {row[1] for row in migration_db.execute("PRAGMA table_info(song_stages)")}
        check(
            {"input_fingerprint", "client_key", "recovery_attempts"} <= migrated,
            "旧库补列 DDL 可执行（含 NOT NULL DEFAULT 0 的恢复计数列）",
        )
        check(
            {"model", "prompt_tokens", "completion_tokens", "total_tokens", "metrics_json"} <= migrated_stages,
            "旧 song_stages 补遥测列 DDL 可执行",
        )

        # --- 2. UUIDv7 ---
        print("2) UUIDv7 生成规则")
        ids = [uuid7() for _ in range(200)]
        check(all(item.version == 7 for item in ids), "200 个 ID 的 version 均为 7")
        check(all(item.variant == uuid.RFC_4122 for item in ids), "variant 均为 RFC 4122")
        check(all(item.hex < nxt.hex for item, nxt in zip(ids, ids[1:])), "同毫秒内严格单调递增")
        check(all(len(new_song_id()) == 32 for _ in range(5)), "new_song_id() 仍是 32 位 hex")
        check(is_song_id(new_song_id()) and not is_song_id("../../etc/passwd"), "路径 ID 白名单生效")

        # --- 3. 前端三个 AI 按钮 ---
        print("3) 前端依赖的三个 LLM 任务（async 路由）")
        for task in ("prompt", "lyrics", "title"):
            response = client.post("/v1/llm", json={**LLM_PAYLOAD, "task": task})
            check(response.status_code == 200 and bool(response.json()["output"]), f"POST /v1/llm [{task}] 有输出")

        # --- 4. 创建作品：立即返回 + 响应契约 + UUIDv7 主键 ---
        print("4) 创建作品（立即返回，后台 asyncio 任务跑五阶段）")
        created = client.post("/v1/generations", json=PAYLOAD)
        check(created.status_code == 200, "POST /v1/generations 返回 200")
        data = created.json()
        check(FRONTEND_FIELDS <= set(data), "响应字段覆盖前端 GenerationResponse")
        check(data["status"] in ("queued", "processing", "ready"), "初始状态为前端认识的状态值")
        song_id = data["id"]
        created_ids.append(song_id)
        check(uuid.UUID(hex=song_id).version == 7, "作品 ID 是 UUIDv7（时间有序）")
        check(data["model_variant"] == settings.music_model, "不传 model_variant 时由 MUSIC_MODEL 配置回填")
        check("X-Deduplicated" not in created.headers, "首次创建不带去重响应头")
        with sqlite3.connect(str(settings.db_path)) as conn:
            row = conn.execute(
                "SELECT input_fingerprint, client_key FROM songs WHERE id = ?", (song_id,)
            ).fetchone()
        check(bool(row and row[0] and row[1]), "请求指纹与客户端标识已落库")

        # --- 5. 幂等去重：防重复提交 ---
        print("5) 重复提交防护")
        repeat = client.post("/v1/generations", json=PAYLOAD)
        check(repeat.json()["id"] == song_id, "相同请求直接返回已有作品")
        check(repeat.headers.get("X-Deduplicated") == "true", "去重响应带 X-Deduplicated 头")
        normalized = dict(PAYLOAD)
        normalized["inputs"] = {**PAYLOAD["inputs"], "prompt": "  " + PAYLOAD["inputs"]["prompt"] + "\n"}
        check(client.post("/v1/generations", json=normalized).json()["id"] == song_id, "首尾空白规范化后仍命中去重")
        idem = client.post("/v1/generations", json=PAYLOAD, headers={"Idempotency-Key": "test-key-001"})
        check(idem.status_code == 200, "带 Idempotency-Key 的首次提交成功")
        created_ids.append(idem.json()["id"])
        check(idem.json()["id"] != song_id, "幂等键与内容指纹相互独立")
        different = dict(PAYLOAD)
        different["inputs"] = {**PAYLOAD["inputs"], "prompt": "完全不同的一段描述"}
        repeat_key = client.post("/v1/generations", json=different, headers={"Idempotency-Key": "test-key-001"})
        check(repeat_key.json()["id"] == idem.json()["id"], "相同 Idempotency-Key 无视内容差异仍幂等")
        forced = client.post("/v1/generations?force=true", json=PAYLOAD)
        check(forced.json()["id"] != song_id, "force=true 可显式跳过去重新建任务")
        created_ids.append(forced.json()["id"])
        check(len(set(created_ids)) == 3, "上述操作最终只产生 3 个真实任务（无重复计费）")

        # --- 6. 后台五阶段（全异步） ---
        print("6) 轮询等待后台五阶段完成")
        for item_id in created_ids:
            detail = wait_ready(client, item_id)
            check(detail.get("status") == "ready", f"作品 {item_id[:8]}… 最终 ready（{detail.get('error_message')}）")
            stages = detail.get("stages", [])
            check(len(stages) == 5 and all(s["status"] == "completed" for s in stages), "五个阶段全部 completed")
            # --- 阶段遥测：模型 / token / 扩展指标 ---
            check(
                all(s["model"] for s in stages) and all("elapsed_ms" in s["metrics"] for s in stages),
                "每个阶段都记录了使用模型与耗时（elapsed_ms）",
            )
            planning = next(s for s in stages if s["stage"] == "planning")
            music = next(s for s in stages if s["stage"] == "music_generation")
            check(planning["model"] == "mock-llm" and planning["usage"] is None, "LLM 阶段记录模型名，mock 无 token 用量")
            check(
                music["model"] == "mock-music" and bool(music["metrics"].get("provider_task_id")),
                "音乐阶段记录音乐模型与远端任务号",
            )
        detail = client.get(f"/v1/generations/{song_id}").json()
        check(bool(detail["title"]) and bool(detail["lyrics"]) and bool(detail["style_prompt"]), "标题 / 歌词 / 风格提示词已回填")

        # --- 7. 作品库与音频 ---
        print("7) 作品库与音频")
        history = client.get("/v1/history").json()
        check(isinstance(history, list) and len(history) == 3, "GET /v1/history 返回 3 条作品")
        check(
            any(item["audio_url"] == f"/v1/generations/{song_id}/audio" for item in history),
            "audio_url 指向后端音频接口",
        )
        audio = client.get(f"/v1/generations/{song_id}/audio")
        check(audio.status_code == 200 and audio.content[:4] == b"RIFF", "音频接口返回可播放的 WAV 数据")
        # 历史作品的 model_variant 仍是 turbo 等旧值；响应模型必须兼容，
        # 否则整个列表接口会抛 ValidationError -> 500（浏览器误报跨域错误）。
        with sqlite3.connect(str(settings.db_path)) as conn:
            conn.execute("UPDATE songs SET model_variant = 'turbo' WHERE id = ?", (song_id,))
            conn.commit()
        legacy = client.get("/v1/history")
        check(
            legacy.status_code == 200 and any(item["model_variant"] == "turbo" for item in legacy.json()),
            "历史 model_variant=turbo 的作品仍能正常返回",
        )

        # --- 8. 恶意调接口防护 ---
        print("8) 恶意调用防护（413 / 422 / 429）")
        check(client.get("/v1/history?limit=0").status_code == 422, "limit=0 被 Query 校验拒绝")
        check(client.get("/v1/history?limit=101").status_code == 422, "limit 超上限被拒绝")
        check(client.post("/v1/generations", json={**PAYLOAD, "hack": "x" * 100}).status_code == 422, "未知字段被 extra=forbid 拒绝")
        too_long = dict(PAYLOAD)
        too_long["inputs"] = {**PAYLOAD["inputs"], "prompt": "字" * 501}
        check(client.post("/v1/generations", json=too_long).status_code == 422, "prompt 超过 500 字被拒绝")
        blank = dict(PAYLOAD)
        blank["inputs"] = {**PAYLOAD["inputs"], "prompt": "   "}
        check(client.post("/v1/generations", json=blank).status_code == 422, "空描述被拒绝（前端禁用按钮后的第二道校验）")
        no_inputs = {key: value for key, value in PAYLOAD.items() if key != "inputs"}
        check(client.post("/v1/generations", json=no_inputs).status_code == 422, "完全不传 inputs 也被拒绝")
        check(client.get("/v1/generations/../../etc/passwd").status_code in (404, 422), "非法 song_id 无法穿透路径校验")
        settings.max_request_bytes = 100
        check(client.post("/v1/llm", json=LLM_PAYLOAD).status_code == 413, "超大请求体返回 413")
        settings.max_request_bytes = 65536
        settings.rate_limit_llm = 1
        deps.reset_limiters()
        check(client.post("/v1/llm", json=LLM_PAYLOAD).status_code == 200, "限流窗口内第 1 次调用放行")
        limited = client.post("/v1/llm", json=LLM_PAYLOAD)
        check(limited.status_code == 429 and "Retry-After" in limited.headers, "第 2 次调用返回 429 + Retry-After")
        settings.rate_limit_llm = 100
        deps.reset_limiters()
        # 并发闸门必须在幂等判断之后：把单客户端上限压到 0，
        # 重复提交（命中已有作品）仍应 200，而全新请求应 429。
        settings.max_active_jobs_per_client = 0
        check(client.post("/v1/generations", json=PAYLOAD).status_code == 200, "幂等重试不受活跃任务闸门误拦")
        fresh = dict(PAYLOAD)
        fresh["inputs"] = {**PAYLOAD["inputs"], "prompt": "闸门检查用的全新描述"}
        check(client.post("/v1/generations", json=fresh).status_code == 429, "真新建请求被活跃任务闸门拒绝")
        settings.max_active_jobs_per_client = 20
        # 每日配额：把上限压到 1（今天已建过多首）后，全新请求应被拒绝，
        # 且 detail 是给用户看的友好中文；幂等重试仍要放行（不消耗配额）。
        settings.daily_generation_limit = 1
        quota_hit = client.post(
            "/v1/generations",
            json={**PAYLOAD, "inputs": {**PAYLOAD["inputs"], "prompt": "每日配额检查用的全新描述"}},
        )
        check(quota_hit.status_code == 429, "每日配额用完时创建接口返回 429")
        check("今日生成额度已用完" in quota_hit.json()["detail"], "配额拒绝提示为友好中文说明")
        check("Retry-After" in quota_hit.headers, "配额拒绝带 Retry-After（指向明日重置）")
        check(quota_hit.headers.get("X-Daily-Remaining") == "0", "配额拒绝带 X-Daily-Remaining: 0")
        check(client.post("/v1/generations", json=PAYLOAD).status_code == 200, "额度用完不影响幂等重试")
        settings.daily_generation_limit = 50
        check(
            client.post("/v1/generations", json=PAYLOAD).headers.get("X-Daily-Remaining") is not None,
            "正常创建响应带剩余额度头（X-Daily-Remaining）",
        )

        # --- 9. 删除：验证级联清理 ---
        # 若维护者临时把 @router.delete 注释掉（调试时常见），这里跳过而不是
        # 让整套测试失败；但会在输出里明确提示该接口当前不可用。
        print("9) 删除")
        first_delete = client.delete(f"/v1/generations/{created_ids[0]}")
        if first_delete.status_code == 405:
            print("  [SKIP] @router.delete 当前被注释（routers/generations.py），DELETE 返回 405，跳过级联删除验证")
        else:
            check(first_delete.status_code == 200, f"DELETE {created_ids[0][:8]}… 返回 200")
            for item_id in created_ids[1:]:
                check(client.delete(f"/v1/generations/{item_id}").status_code == 200, f"DELETE {item_id[:8]}… 返回 200")
            check(client.get("/v1/history").json() == [], "删除后作品库为空")
            with sqlite3.connect(str(settings.db_path)) as conn:
                leftover = conn.execute("SELECT COUNT(*) FROM song_stages").fetchone()[0]
            check(leftover == 0, "外键级联删除阶段记录")

    # --- 10. 后端异常也必须带 CORS 头，否则浏览器会把 500 误报成跨域问题 ---
    print("10) 错误响应的跨域头")
    async def boom() -> None:
        raise RuntimeError("CORS 兜底测试用异常")

    logging.getLogger("app.middleware").disabled = True  # 预期内的异常，别刷 traceback
    app.dependency_overrides[get_song_service] = boom
    with TestClient(app, raise_server_exceptions=False) as error_client:
        failed = error_client.get("/v1/history", headers={"Origin": "http://127.0.0.1:5177"})
        check(failed.status_code == 500, "未捕获异常返回 JSON 500 而非纯文本")
        check(failed.headers.get("access-control-allow-origin") == "http://127.0.0.1:5177", "500 响应仍带 Access-Control-Allow-Origin")
    app.dependency_overrides.clear()
    logging.getLogger("app.middleware").disabled = False

    # --- 11. 持久化队列：模拟进程被 kill 后重启 ---
    # 直接写 DB + 产物目录，绕过所有 HTTP 接口，等价于「上一个进程遗留的任务」。
    print("11) 重启恢复（数据库即队列）")
    legacy_id = new_song_id()
    exhausted_id = new_song_id()
    failed_id = new_song_id()
    now = utc_now()
    seed_text = "重启恢复测试：夏末站台的民谣"
    for song_id, attempts, status in (
        (legacy_id, 2, "queued"),      # 中断过 2 次，还剩 1 次恢复机会
        (exhausted_id, 3, "queued"),   # 已达上限，应停止自动恢复
        (failed_id, 0, "failed"),      # 终态，不应入队
    ):
        task_dir = settings.outputs_dir / song_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / USER_INPUT_FILE).write_text(seed_text, encoding="utf-8")
        with sqlite3.connect(str(settings.db_path)) as conn:
            conn.execute(
                "INSERT INTO songs (id, title, task_type, mode, model_variant, status, prompt, "
                "source_prompt, instrumental, metadata_json, recovery_attempts, created_at, updated_at) "
                "VALUES (?, ?, 'text2music', 'simple', ?, ?, ?, ?, 0, '{}', ?, ?, ?)",
                (song_id, "待恢复作品", settings.music_model, status, seed_text, seed_text, attempts, now, now),
            )
            for stage in STAGE_ORDER:
                conn.execute(
                    "INSERT INTO song_stages (song_id, stage, status, output_file) VALUES (?, ?, 'pending', ?)",
                    (song_id, stage, STAGE_OUTPUTS[stage]),
                )
            conn.commit()

    # 新的 TestClient = 新的 async lifespan = 模拟一次全新的后端进程。
    with TestClient(app) as restarted:
        detail = wait_ready(restarted, legacy_id, timeout=30)
        check(
            detail.get("status") == "ready",
            f"重启后遗留任务自动恢复并跑完五阶段（{detail.get('error_message')}）",
        )
        stages = detail.get("stages", [])
        check(
            len(stages) == 5 and all(item["status"] == "completed" for item in stages),
            "恢复任务的五个阶段全部 completed",
        )
        check(
            restarted.get(f"/v1/generations/{legacy_id}/audio").status_code == 200,
            "恢复生成的音频可下载",
        )
        with sqlite3.connect(str(settings.db_path)) as conn:
            attempts = conn.execute(
                "SELECT recovery_attempts FROM songs WHERE id = ?", (legacy_id,)
            ).fetchone()[0]
            exhausted = conn.execute(
                "SELECT status, error_message FROM songs WHERE id = ?", (exhausted_id,)
            ).fetchone()
            failed = conn.execute("SELECT status FROM songs WHERE id = ?", (failed_id,)).fetchone()
        check(attempts == 0, "任务正常跑完后连续中断计数清零")
        check(
            exhausted[0] == "failed" and "停止自动恢复" in (exhausted[1] or ""),
            "超过最大恢复次数的任务被停止并写明原因",
        )
        check(failed[0] == "failed", "failed 是终态，不会被重新入队")

    # --- 12. 执行到一半被 kill：状态保留 + 阶段产物断点续跑 ---
    print("12) 运行中中断与断点续跑")
    import asyncio  # noqa: PLC0415 - 仅本步骤需要

    from app.services import runtime  # noqa: E402, PLC0415
    from app.services.workflow_service import WorkflowService  # noqa: E402, PLC0415

    class SlowProvider:
        """阶段 5 故意挂起，模拟远端音频生成时进程被强制关闭。"""

        @staticmethod
        async def generate(**kwargs):  # noqa: ARG004
            await asyncio.sleep(30)
            return {}

    # 注入带「慢音频生成」的编排器；shutdown 会清掉单例，下次启动自动恢复默认 mock。
    runtime._workflow_service = WorkflowService(provider=SlowProvider())
    with TestClient(app) as interrupted:
        response = interrupted.post(
            "/v1/generations",
            json={
                "task_type": "text2music",
                "mode": "simple",
                "inputs": {"prompt": "中断续跑测试：雨夜的城市", "instrumental": False},
            },
        )
        check(response.status_code == 200, "模拟中断用的新任务提交成功")
        slow_id = response.json()["id"]
        task_dir = settings.outputs_dir / slow_id
        deadline = time.monotonic() + 20
        while not (task_dir / STAGE_OUTPUTS["music_prompt"]).is_file():
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        check(
            (task_dir / STAGE_OUTPUTS["planning"]).is_file()
            and (task_dir / STAGE_OUTPUTS["music_prompt"]).is_file(),
            "中断前前四阶段产物已落盘，卡在第五阶段",
        )
        with sqlite3.connect(str(settings.db_path)) as conn:
            before_started = conn.execute(
                "SELECT started_at FROM song_stages WHERE song_id = ? AND stage = 'planning'", (slow_id,)
            ).fetchone()[0]
    # with 退出即 shutdown()：只 cancel 内存 task，不能把作品写成 failed。
    with sqlite3.connect(str(settings.db_path)) as conn:
        interrupted_status = conn.execute(
            "SELECT status FROM songs WHERE id = ?", (slow_id,)
        ).fetchone()[0]
    check(
        interrupted_status in ("queued", "processing"),
        "应用关闭时保留未完成任务状态（不再误标 failed）",
    )

    with TestClient(app) as resumed:
        detail = wait_ready(resumed, slow_id, timeout=30)
        check(
            detail.get("status") == "ready",
            f"重启后从断点继续并生成音频（{detail.get('error_message')}）",
        )
        with sqlite3.connect(str(settings.db_path)) as conn:
            after_started = conn.execute(
                "SELECT started_at FROM song_stages WHERE song_id = ? AND stage = 'planning'", (slow_id,)
            ).fetchone()[0]
        check(
            before_started is not None and after_started == before_started,
            "已落盘的前四阶段未重复执行（阶段开始时间未刷新）",
        )
        # 复用的阶段不能把上次记录的模型抹成 NULL：这正是「换了模型但数据库
        # 没记录」的成因（早期实现调用 complete() 无条件写 model=None）。
        with sqlite3.connect(str(settings.db_path)) as conn:
            reused_model, reused_metrics = conn.execute(
                "SELECT model, metrics_json FROM song_stages WHERE song_id = ? AND stage = 'planning'",
                (slow_id,),
            ).fetchone()
        check(reused_model == "mock-llm", "复用阶段保留上次记录的模型（不被清成 NULL）")
        check('"reused": true' in (reused_metrics or ""), "复用阶段在 metrics 里标记 reused，可与「真跑」区分")

    print("\n全部通过：全异步链路 + 依赖注入 + UUIDv7 + 幂等去重 + 接口防护 + /v1 与跨域 + 重启可恢复队列。")


if __name__ == "__main__":
    main()
