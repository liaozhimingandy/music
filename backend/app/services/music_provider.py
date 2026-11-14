"""service 层：ModelScope ACE-Step 异步音乐生成客户端。

保留原型的四项能力：参数指纹去重、task_id 断点续跑、已完成直接返回、
生成成功但下载失败时只重新下载。改用 httpx 统一网络栈。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import struct
import time
import wave
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from ..config import settings
from ..constants import FINAL_AUDIO_NAME, MUSIC_TASK_FILE
from ..dal import ArtifactStore

logger = logging.getLogger(__name__)


class MusicProvider:
    def __init__(self, config: Any = None) -> None:
        cfg = config or settings
        self.base_url = cfg.music_api_base_url.rstrip("/")
        self.token = cfg.music_api_token
        self.model = cfg.music_model
        self.poll_interval = max(1, int(cfg.music_poll_interval))
        self.timeout = max(1, int(cfg.music_timeout))
        self.download_timeout = cfg.music_download_timeout

    def generate(
        self,
        *,
        prompt: str,
        lyrics: str,
        duration: int,
        work_dir: Path,
        force_regenerate: bool = False,
    ) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("未配置 MUSIC_API_TOKEN，请在 backend/app/.env 中填写")
        if not prompt or not prompt.strip():
            raise ValueError("音乐描述 prompt 不能为空")
        if not lyrics or not lyrics.strip():
            raise ValueError("歌词 lyrics 不能为空")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise ValueError("音乐时长 duration 必须大于 0")

        duration = int(duration)
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        store = ArtifactStore(work_dir, resume=True)

        fingerprint_data = {"model": self.model, "prompt": prompt, "lyrics": lyrics, "duration": duration}
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        task: dict[str, Any] | None = None
        if store.exists(MUSIC_TASK_FILE):
            task = store.load_json(MUSIC_TASK_FILE)
            if task.get("fingerprint") != fingerprint:
                if not force_regenerate:
                    raise RuntimeError("当前任务目录已存在其他参数的生成记录，请使用新目录或 force_regenerate=True")
                task = None
            elif task.get("status") == "completed":
                audio_path = Path(task.get("audio_path") or "")
                if audio_path.is_file():
                    logger.info("检测到已完成的音乐任务，直接返回：%s", task.get("task_id"))
                    return self._result(task, audio_path, duration)
            elif task.get("status") == "failed" and not force_regenerate:
                raise RuntimeError(
                    f"上一次音乐生成失败（任务 {task.get('task_id')}），如需重提交请设置 force_regenerate=True"
                )
            elif task.get("status") == "failed":
                task = None

        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        with httpx.Client(timeout=60.0) as client:
            if task is None:
                logger.info("提交音乐生成任务，模型=%s，时长=%ss", self.model, duration)
                response = client.post(
                    f"{self.base_url}/v1/audios/generations",
                    headers={**headers, "X-ModelScope-Async-Mode": "true"},
                    json={"model": self.model, "prompt": prompt, "lyrics": lyrics, "duration": duration},
                )
                if not response.is_success:
                    raise RuntimeError(f"音乐任务提交失败：HTTP {response.status_code}，{response.text}")
                submitted = response.json()
                if submitted.get("status") == "FAILED":
                    raise RuntimeError(f"音乐任务提交失败：{submitted}")
                task_id = submitted.get("task_id")
                if not task_id:
                    raise RuntimeError(f"接口未返回 task_id：{submitted}")
                task = {
                    "fingerprint": fingerprint,
                    "task_id": task_id,
                    "model": self.model,
                    "status": "submitted",
                    "audio_url": None,
                    "audio_path": None,
                    "duration": duration,
                }
                store.save_json(task, MUSIC_TASK_FILE)
            else:
                logger.info("恢复已有音乐任务：%s", task.get("task_id"))

            task_id = task["task_id"]

            if task.get("status") != "generated":
                started = time.monotonic()
                last_status: str | None = None
                while True:
                    response = client.get(
                        f"{self.base_url}/v1/tasks/{task_id}",
                        headers={**headers, "X-ModelScope-Task-Type": "audio_generation"},
                    )
                    if not response.is_success:
                        raise RuntimeError(f"查询音乐任务失败：HTTP {response.status_code}，{response.text}")
                    polled = response.json()
                    status = polled.get("task_status")
                    if status == "SUCCEED":
                        audio_url = (polled.get("outputs") or {}).get("url")
                        if not audio_url:
                            raise RuntimeError(f"任务成功但未返回音频地址：{polled}")
                        task.update(status="generated", audio_url=audio_url)
                        store.save_json(task, MUSIC_TASK_FILE)
                        break
                    if status == "FAILED":
                        task.update(status="failed", error=polled)
                        store.save_json(task, MUSIC_TASK_FILE)
                        raise RuntimeError(f"音乐生成失败：{polled}")
                    if time.monotonic() - started >= self.timeout:
                        # 不改状态，下次可继续轮询同一个 task_id
                        raise TimeoutError(f"等待音乐生成超时（任务 {task_id}），下次运行可继续恢复")
                    if status != last_status:
                        logger.info("音乐任务状态：%s", status)
                        last_status = status
                    time.sleep(self.poll_interval)

            audio_url = task["audio_url"]
            suffix = Path(urlparse(audio_url).path).suffix or ".audio"
            output_path = work_dir / f"{FINAL_AUDIO_NAME}{suffix}"
            logger.info("开始下载音频：%s", audio_url)
            with client.stream("GET", audio_url, timeout=self.download_timeout) as download:
                download.raise_for_status()
                with output_path.open("wb") as handle:
                    for chunk in download.iter_bytes(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)

            task.update(status="completed", audio_path=str(output_path))
            store.save_json(task, MUSIC_TASK_FILE)
            return self._result(task, output_path, duration)

    @staticmethod
    def _result(task: dict[str, Any], audio_path: Path, duration: int) -> dict[str, Any]:
        return {
            "task_id": task["task_id"],
            "model": task["model"],
            "status": "completed",
            "audio_url": task["audio_url"],
            "audio_path": str(audio_path),
            "duration": duration,
        }


class MockMusicProvider:
    """离线演示：本地合成一段短 WAV，让前端播放/进度条可用，不产生任何网络请求。"""

    model = "mock-music"

    def generate(
        self,
        *,
        prompt: str,  # noqa: ARG002
        lyrics: str,  # noqa: ARG002
        duration: int,
        work_dir: Path,
        force_regenerate: bool = False,  # noqa: ARG002
    ) -> dict[str, Any]:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        store = ArtifactStore(work_dir, resume=True)
        audio_path = work_dir / f"{FINAL_AUDIO_NAME}.wav"

        sample_rate = 11025
        seconds = 8
        with wave.open(str(audio_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            frames = bytearray()
            for index in range(sample_rate * seconds):
                t = index / sample_rate
                fade = min(1.0, t * 2, (seconds - t) * 2)
                value = sum(math.sin(2 * math.pi * 220 * ratio * t) / (i + 1) for i, ratio in enumerate((1, 1.25, 1.5)))
                frames.extend(struct.pack("<h", int(value * fade * 8000)))
            wav.writeframes(bytes(frames))

        task_id = f"mock-{uuid4().hex[:12]}"
        store.save_json(
            {"task_id": task_id, "status": "completed", "audio_url": "mock://local", "audio_path": str(audio_path)},
            MUSIC_TASK_FILE,
        )
        return {
            "task_id": task_id,
            "model": self.model,
            "status": "completed",
            "audio_url": "mock://local",
            "audio_path": str(audio_path),
            "duration": int(duration),
        }


def build_music_provider(config: Any = None) -> MusicProvider | MockMusicProvider:
    cfg = config or settings
    if cfg.mock_mode or not cfg.music_api_token:
        return MockMusicProvider()
    return MusicProvider(cfg)
