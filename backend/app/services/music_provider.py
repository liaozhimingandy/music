"""service 层：ModelScope ACE-Step 异步音乐生成客户端（全异步）。

保留原型的四项能力（这是本模块最需要维护者理解的约定）：

1. **参数指纹去重**：model + prompt + lyrics + duration 做 sha256，写入
   ``music_task.json``；同目录换了参数会拒绝复用，避免张冠李戴。
2. **task_id 断点续跑**：提交后把 task_id 落盘，进程重启可继续轮询同一个
   远端任务，不重复计费、不重复生成。
3. **已完成直接返回**：状态 completed 且本地音频文件仍在时直接返回结果。
4. **下载失败只重下**：状态 generated 表示远端已成功，下次跳过轮询直接
   重新下载音频。

网络请求统一用 ``httpx.AsyncClient``：提交 / 轮询 / 流式下载都 ``await``，
轮询等待用 ``asyncio.sleep``，所以等待远端生成的几分钟里 event loop 仍可
正常响应前端轮询。状态流转大致为
``submitted -> (polling) -> generated -> completed``，异常分支为 ``failed``。
"""

from __future__ import annotations

import asyncio
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

import httpx

from ..config import settings
from ..constants import FINAL_AUDIO_NAME, MUSIC_TASK_FILE
from ..dal import ArtifactStore
from ..ids import new_provider_task_id

logger = logging.getLogger(__name__)


class MusicProvider:
    """真实音乐生成客户端：提交异步任务 -> 轮询 -> 流式下载音频。

    Args:
        config: 可注入的配置对象；缺省使用全局 settings。
            关键项：MUSIC_API_TOKEN / MUSIC_API_BASE_URL / MUSIC_MODEL /
            MUSIC_POLL_INTERVAL / MUSIC_TIMEOUT / MUSIC_DOWNLOAD_TIMEOUT。
    """

    def __init__(self, config: Any = None) -> None:
        cfg = config or settings
        self.base_url = cfg.music_api_base_url.rstrip("/")
        self.token = cfg.music_api_token
        self.model = cfg.music_model
        self.poll_interval = max(1, int(cfg.music_poll_interval))
        self.timeout = max(1, int(cfg.music_timeout))
        self.download_timeout = cfg.music_download_timeout

    async def generate(
        self,
        *,
        prompt: str,
        lyrics: str,
        duration: int,
        work_dir: Path,
        force_regenerate: bool = False,
    ) -> dict[str, Any]:
        """生成一段音频并返回任务结果（含本地 ``audio_path``）。

        Args:
            prompt: 阶段四产出的风格描述（style）。
            lyrics: 最终歌词；纯音乐时由调用方传入 ``[Instrumental]``。
            duration: 目标时长（秒）。
            work_dir: 该歌曲的 ``music/`` 工作目录，存放 music_task.json 与音频。
            force_regenerate: 忽略已有记录、强制重新提交；用于参数变更或上次失败。

        返回字典键：task_id / model / status / audio_url / audio_path / duration。
        """
        # --- 入参校验：这些错误不值得发网络请求 ---
        if not self.token:
            raise RuntimeError("未配置 MUSIC_API_TOKEN，请在 backend/.env 中填写")
        if not prompt or not prompt.strip():
            raise ValueError("音乐描述 prompt 不能为空")
        if not lyrics or not lyrics.strip():
            raise ValueError("歌词 lyrics 不能为空")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise ValueError("音乐时长 duration 必须大于 0")

        duration = int(duration)
        work_dir = Path(work_dir)
        await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
        store = ArtifactStore(work_dir, resume=True)

        # 指纹用 sort_keys 保证字典顺序变化不会影响哈希结果。
        fingerprint_data = {"model": self.model, "prompt": prompt, "lyrics": lyrics, "duration": duration}
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        # --- 读取已有任务记录，决定复用 / 续跑 / 拒绝 / 重提交 ---
        task: dict[str, Any] | None = None
        if await store.exists(MUSIC_TASK_FILE):
            task = await store.load_json(MUSIC_TASK_FILE)
            if task.get("fingerprint") != fingerprint:
                # 目录被别的参数占用：默认拒绝，避免返回不匹配的音频。
                if not force_regenerate:
                    raise RuntimeError("当前任务目录已存在其他参数的生成记录，请使用新目录或 force_regenerate=True")
                task = None
            elif task.get("status") == "completed":
                audio_path = Path(task.get("audio_path") or "")
                if await asyncio.to_thread(audio_path.is_file):
                    logger.info("检测到已完成的音乐任务，直接返回：%s", task.get("task_id"))
                    return self._result(task, audio_path, duration)
            elif task.get("status") == "failed" and not force_regenerate:
                raise RuntimeError(
                    f"上一次音乐生成失败（任务 {task.get('task_id')}），如需重提交请设置 force_regenerate=True"
                )
            elif task.get("status") == "failed":
                task = None

        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            # --- 首次提交：异步模式头不可少，否则接口会同步等待直到超时 ---
            if task is None:
                logger.info("提交音乐生成任务，模型=%s，时长=%ss", self.model, duration)
                response = await client.post(
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
                await store.save_json(task, MUSIC_TASK_FILE)
            else:
                logger.info("恢复已有音乐任务：%s", task.get("task_id"))

            task_id = task["task_id"]

            # --- 轮询远端状态；generated 表示已成功并拿到了音频 URL ---
            if task.get("status") != "generated":
                started = time.monotonic()
                last_status: str | None = None
                while True:
                    response = await client.get(
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
                        await store.save_json(task, MUSIC_TASK_FILE)
                        break
                    if status == "FAILED":
                        task.update(status="failed", error=polled)
                        await store.save_json(task, MUSIC_TASK_FILE)
                        raise RuntimeError(f"音乐生成失败：{polled}")
                    if time.monotonic() - started >= self.timeout:
                        # 不改状态，下次可继续轮询同一个 task_id
                        raise TimeoutError(f"等待音乐生成超时（任务 {task_id}），下次运行可继续恢复")
                    if status != last_status:
                        logger.info("音乐任务状态：%s", status)
                        last_status = status
                    # 异步等待，不阻塞 event loop 上的其它请求。
                    await asyncio.sleep(self.poll_interval)

            # --- 下载音频：流式写盘，避免大文件占满内存 ---
            audio_url = task["audio_url"]
            suffix = Path(urlparse(audio_url).path).suffix or ".audio"
            output_path = work_dir / f"{FINAL_AUDIO_NAME}{suffix}"
            logger.info("开始下载音频：%s", audio_url)
            async with client.stream("GET", audio_url, timeout=self.download_timeout) as download:
                download.raise_for_status()
                with output_path.open("wb") as handle:
                    async for chunk in download.aiter_bytes(chunk_size=1024 * 1024):
                        if chunk:
                            await asyncio.to_thread(handle.write, chunk)

            task.update(status="completed", audio_path=str(output_path))
            await store.save_json(task, MUSIC_TASK_FILE)
            return self._result(task, output_path, duration)

    @staticmethod
    def _result(task: dict[str, Any], audio_path: Path, duration: int) -> dict[str, Any]:
        """统一结果结构，供 WorkflowService 写入 songs 表。"""
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

    @staticmethod
    def _synth_sync(audio_path: Path) -> None:
        """同步合成 8 秒 11kHz 单声道 WAV（三谐波 + 首尾淡入淡出）。

        CPU 密集循环放在线程池执行，避免阻塞 event loop；实际写盘也在这里完成。
        """
        sample_rate = 11025
        seconds = 8
        with wave.open(str(audio_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            frames = bytearray()
            for index in range(sample_rate * seconds):
                t = index / sample_rate
                fade = min(1.0, t * 2, (seconds - t) * 2)  # 首尾各 0.5s 淡入淡出，避免爆音
                value = sum(math.sin(2 * math.pi * 220 * ratio * t) / (i + 1) for i, ratio in enumerate((1, 1.25, 1.5)))
                frames.extend(struct.pack("<h", int(value * fade * 8000)))
            wav.writeframes(bytes(frames))

    async def generate(
        self,
        *,
        prompt: str,  # noqa: ARG002
        lyrics: str,  # noqa: ARG002
        duration: int,
        work_dir: Path,
        force_regenerate: bool = False,  # noqa: ARG002
    ) -> dict[str, Any]:
        """写入 8 秒 WAV 并返回与真实实现同构的结果。

        固定 8 秒而非请求时长，是为了让本地演示秒级完成；``duration``
        仍原样回填到结果里，保证前端展示与真实模式一致。
        """
        work_dir = Path(work_dir)
        await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
        store = ArtifactStore(work_dir, resume=True)
        audio_path = work_dir / f"{FINAL_AUDIO_NAME}.wav"
        await asyncio.to_thread(self._synth_sync, audio_path)

        # 演示任务 ID 同样用 UUIDv7，保证日志 / 任务文件里的时间有序。
        task_id = new_provider_task_id()
        await store.save_json(
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
    """工厂：mock 模式或缺 token 时返回 MockMusicProvider，否则返回真实客户端。"""
    cfg = config or settings
    if cfg.mock_mode or not cfg.music_api_token:
        return MockMusicProvider()
    return MusicProvider(cfg)
