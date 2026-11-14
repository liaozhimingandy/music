"""端到端冒烟测试（离线 mock，不调用任何外部 API、不消耗密钥）。

运行：backend/.venv/Scripts/python.exe backend/tests/smoke_test.py
覆盖：建表 → 健康检查 → LLM 三任务 → 创建作品 → 后台五阶段 → 音频 → 删除。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

TMP = Path(__file__).resolve().parent / ".tmp"
os.environ["MUSIC_STUDIO_MOCK"] = "true"
os.environ["MUSIC_STUDIO_DATA_DIR"] = str(TMP / "data")
shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

FRONTEND_FIELDS = {
    "id", "title", "task_type", "mode", "model_variant", "status", "prompt", "lyrics",
    "instrumental", "duration_seconds", "bpm", "key", "time_signature", "audio_url",
    "cover_color", "cover_icon", "cover_image_url", "error_message", "metadata",
    "created_at", "updated_at",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  [OK] {message}")


def main() -> None:
    with TestClient(app) as client:
        print("1) 健康检查与建表")
        health = client.get("/health")
        check(health.status_code == 200, "GET /health 返回 200")
        check(health.json()["app"] == "Music Studio", "应用名为 Music Studio")
        check(health.json()["mock_mode"] is True, "离线 mock 模式已生效")
        with sqlite3.connect(str(settings.db_path)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check({"songs", "song_stages"} <= tables, "SQLite 建出 songs / song_stages 两张表")

        print("2) 前端依赖的三个 LLM 任务")
        for task in ("prompt", "lyrics", "title"):
            response = client.post("/api/llm", json={
                "task": task,
                "seed_prompt": "写一首关于夏末告别的华语民谣",
                "instrumental": False,
                "style_tags": [],
            })
            check(response.status_code == 200 and bool(response.json()["output"]), f"POST /api/llm [{task}] 有输出")

        print("3) 创建作品（立即返回，后台跑五阶段）")
        created = client.post("/api/generations", json={
            "title": "夏末告别",
            "task_type": "text2music",
            "mode": "simple",
            "model_variant": "turbo",
            "inputs": {
                "prompt": "夏末站台告别，木吉他与温柔女声",
                "lyrics": "[主歌]\n末班车的灯",
                "instrumental": False,
            },
        })
        check(created.status_code == 200, "POST /api/generations 返回 200")
        data = created.json()
        check(FRONTEND_FIELDS <= set(data), "响应字段覆盖前端 GenerationResponse")
        check(data["status"] in ("queued", "processing", "ready"), "初始状态为前端认识的状态值")
        song_id = data["id"]

        print("4) 轮询等待后台五阶段完成")
        deadline = time.monotonic() + 30
        detail: dict = {}
        while time.monotonic() < deadline:
            detail = client.get(f"/api/generations/{song_id}").json()
            if detail["status"] in ("ready", "failed"):
                break
            time.sleep(0.2)
        check(detail.get("status") == "ready", f"作品最终状态 ready（实际 {detail.get('status')}，错误：{detail.get('error_message')}）")
        stages = detail["stages"]
        check(len(stages) == 5, "生成 5 条阶段记录")
        check(all(item["status"] == "completed" for item in stages), "五个阶段全部 completed")
        check(bool(detail["title"]) and bool(detail["lyrics"]) and bool(detail["style_prompt"]), "标题 / 歌词 / 风格提示词已回填")

        print("5) 作品库与音频")
        history = client.get("/api/history").json()
        check(isinstance(history, list) and len(history) == 1, "GET /api/history 返回 1 条作品")
        check(history[0]["audio_url"] == f"/api/generations/{song_id}/audio", "audio_url 指向后端音频接口")
        audio = client.get(f"/api/generations/{song_id}/audio")
        check(audio.status_code == 200 and audio.content[:4] == b"RIFF", "音频接口返回可播放的 WAV 数据")

        print("6) 删除")
        deleted = client.delete(f"/api/generations/{song_id}")
        check(deleted.status_code == 200, "DELETE /api/generations/{id} 返回 200")
        check(client.get("/api/history").json() == [], "删除后作品库为空")
        with sqlite3.connect(str(settings.db_path)) as conn:
            leftover = conn.execute("SELECT COUNT(*) FROM song_stages").fetchone()[0]
        check(leftover == 0, "外键级联删除阶段记录")

    print("\n全部通过：service -> dal -> model 链路与前端接口契约一致。")


if __name__ == "__main__":
    main()
