"""端到端冒烟测试（离线 mock，不调用任何外部 API、不消耗密钥）。

运行：``backend/.venv/Scripts/python.exe backend/tests/smoke_test.py``
覆盖：建表与旧库补列 → UUIDv7 → 全异步五阶段 → 幂等去重 → 接口防护
（413 / 422 / 429）→ 音频 → 级联删除。

实现要点：环境变量必须在 ``import app`` **之前**设置，因为 Settings 在模块
导入时就实例化；数据目录指向 tests/.tmp（已被 .gitignore 忽略），每次运行
先清空，保证结果可复现、不污染真实作品库。测试里刻意把限流阈值放宽，
只在第 8 步临时调严来断言 429。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
import uuid
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
shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from app import deps  # noqa: E402
from app.config import settings  # noqa: E402
from app.ids import is_song_id, new_song_id, uuid7  # noqa: E402
from app.main import app  # noqa: E402

# 前端 interface/index.ts 里 GenerationResponse 的字段白名单；
# 任何字段改名导致契约破坏，第 4 步就会断言失败。
FRONTEND_FIELDS = {
    "id", "title", "task_type", "mode", "model_variant", "status", "prompt", "lyrics",
    "instrumental", "duration_seconds", "bpm", "key", "time_signature", "audio_url",
    "cover_color", "cover_icon", "cover_image_url", "error_message", "metadata",
    "created_at", "updated_at",
}

PAYLOAD = {
    "title": "夏末告别",
    "task_type": "text2music",
    "mode": "simple",
    "model_variant": "turbo",
    "inputs": {
        "prompt": "夏末站台告别，木吉他与温柔女声",
        "lyrics": "[主歌]\n末班车的灯",
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
        detail = client.get(f"/api/generations/{song_id}").json()
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
        print("1) 健康检查、建表与迁移列")
        health = client.get("/health")
        check(health.status_code == 200, "GET /health 返回 200")
        check(health.json()["app"] == "Music Studio", "应用名为 Music Studio")
        check(health.json()["mock_mode"] is True, "离线 mock 模式已生效")
        with sqlite3.connect(str(settings.db_path)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            columns = {row[1] for row in conn.execute("PRAGMA table_info(songs)")}
        check({"songs", "song_stages"} <= tables, "SQLite 建出 songs / song_stages 两张表")
        check({"input_fingerprint", "client_key"} <= columns, "去重/限流字段已建（含旧库补列逻辑）")

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
            response = client.post("/api/llm", json={**LLM_PAYLOAD, "task": task})
            check(response.status_code == 200 and bool(response.json()["output"]), f"POST /api/llm [{task}] 有输出")

        # --- 4. 创建作品：立即返回 + 响应契约 + UUIDv7 主键 ---
        print("4) 创建作品（立即返回，后台 asyncio 任务跑五阶段）")
        created = client.post("/api/generations", json=PAYLOAD)
        check(created.status_code == 200, "POST /api/generations 返回 200")
        data = created.json()
        check(FRONTEND_FIELDS <= set(data), "响应字段覆盖前端 GenerationResponse")
        check(data["status"] in ("queued", "processing", "ready"), "初始状态为前端认识的状态值")
        song_id = data["id"]
        created_ids.append(song_id)
        check(uuid.UUID(hex=song_id).version == 7, "作品 ID 是 UUIDv7（时间有序）")
        check("X-Deduplicated" not in created.headers, "首次创建不带去重响应头")
        with sqlite3.connect(str(settings.db_path)) as conn:
            row = conn.execute(
                "SELECT input_fingerprint, client_key FROM songs WHERE id = ?", (song_id,)
            ).fetchone()
        check(bool(row and row[0] and row[1]), "请求指纹与客户端标识已落库")

        # --- 5. 幂等去重：防重复提交 ---
        print("5) 重复提交防护")
        repeat = client.post("/api/generations", json=PAYLOAD)
        check(repeat.json()["id"] == song_id, "相同请求直接返回已有作品")
        check(repeat.headers.get("X-Deduplicated") == "true", "去重响应带 X-Deduplicated 头")
        normalized = dict(PAYLOAD)
        normalized["inputs"] = {**PAYLOAD["inputs"], "prompt": "  " + PAYLOAD["inputs"]["prompt"] + "\n"}
        check(client.post("/api/generations", json=normalized).json()["id"] == song_id, "首尾空白规范化后仍命中去重")
        idem = client.post("/api/generations", json=PAYLOAD, headers={"Idempotency-Key": "test-key-001"})
        check(idem.status_code == 200, "带 Idempotency-Key 的首次提交成功")
        created_ids.append(idem.json()["id"])
        check(idem.json()["id"] != song_id, "幂等键与内容指纹相互独立")
        different = dict(PAYLOAD)
        different["inputs"] = {**PAYLOAD["inputs"], "prompt": "完全不同的一段描述"}
        repeat_key = client.post("/api/generations", json=different, headers={"Idempotency-Key": "test-key-001"})
        check(repeat_key.json()["id"] == idem.json()["id"], "相同 Idempotency-Key 无视内容差异仍幂等")
        forced = client.post("/api/generations?force=true", json=PAYLOAD)
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
        detail = client.get(f"/api/generations/{song_id}").json()
        check(bool(detail["title"]) and bool(detail["lyrics"]) and bool(detail["style_prompt"]), "标题 / 歌词 / 风格提示词已回填")

        # --- 7. 作品库与音频 ---
        print("7) 作品库与音频")
        history = client.get("/api/history").json()
        check(isinstance(history, list) and len(history) == 3, "GET /api/history 返回 3 条作品")
        check(
            any(item["audio_url"] == f"/api/generations/{song_id}/audio" for item in history),
            "audio_url 指向后端音频接口",
        )
        audio = client.get(f"/api/generations/{song_id}/audio")
        check(audio.status_code == 200 and audio.content[:4] == b"RIFF", "音频接口返回可播放的 WAV 数据")

        # --- 8. 恶意调接口防护 ---
        print("8) 恶意调用防护（413 / 422 / 429）")
        check(client.get("/api/history?limit=0").status_code == 422, "limit=0 被 Query 校验拒绝")
        check(client.get("/api/history?limit=101").status_code == 422, "limit 超上限被拒绝")
        check(client.post("/api/generations", json={**PAYLOAD, "hack": "x" * 100}).status_code == 422, "未知字段被 extra=forbid 拒绝")
        too_long = dict(PAYLOAD)
        too_long["inputs"] = {**PAYLOAD["inputs"], "prompt": "字" * 501}
        check(client.post("/api/generations", json=too_long).status_code == 422, "prompt 超过 500 字被拒绝")
        check(client.get("/api/generations/../../etc/passwd").status_code in (404, 422), "非法 song_id 无法穿透路径校验")
        settings.max_request_bytes = 100
        check(client.post("/api/llm", json=LLM_PAYLOAD).status_code == 413, "超大请求体返回 413")
        settings.max_request_bytes = 65536
        settings.rate_limit_llm = 1
        deps.reset_limiters()
        check(client.post("/api/llm", json=LLM_PAYLOAD).status_code == 200, "限流窗口内第 1 次调用放行")
        limited = client.post("/api/llm", json=LLM_PAYLOAD)
        check(limited.status_code == 429 and "Retry-After" in limited.headers, "第 2 次调用返回 429 + Retry-After")
        settings.rate_limit_llm = 100
        deps.reset_limiters()
        # 并发闸门必须在幂等判断之后：把单客户端上限压到 0，
        # 重复提交（命中已有作品）仍应 200，而全新请求应 429。
        settings.max_active_jobs_per_client = 0
        check(client.post("/api/generations", json=PAYLOAD).status_code == 200, "幂等重试不受活跃任务闸门误拦")
        fresh = dict(PAYLOAD)
        fresh["inputs"] = {**PAYLOAD["inputs"], "prompt": "闸门检查用的全新描述"}
        check(client.post("/api/generations", json=fresh).status_code == 429, "真新建请求被活跃任务闸门拒绝")
        settings.max_active_jobs_per_client = 20

        # --- 9. 删除：验证级联清理 ---
        print("9) 删除")
        for item_id in created_ids:
            check(client.delete(f"/api/generations/{item_id}").status_code == 200, f"DELETE {item_id[:8]}… 返回 200")
        check(client.get("/api/history").json() == [], "删除后作品库为空")
        with sqlite3.connect(str(settings.db_path)) as conn:
            leftover = conn.execute("SELECT COUNT(*) FROM song_stages").fetchone()[0]
        check(leftover == 0, "外键级联删除阶段记录")

    print("\n全部通过：全异步链路 + 依赖注入 + UUIDv7 + 幂等去重 + 接口防护。")


if __name__ == "__main__":
    main()
