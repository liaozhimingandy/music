"""service 层：面向 API 的作品服务（全异步 + 幂等去重）。

职责：把前端 GenerationCreate 转成数据库记录 -> 启动后台生成任务 ->
把 SongRecord 映射成前端需要的 GenerationResponse。
这是「前端契约」与「数据库实体」之间唯一的转换点，改字段优先改这里。

生成接口的两道防护
------------------
1. **幂等去重**：对规范化后的请求内容算 sha256 指纹（或直接用客户端的
   ``Idempotency-Key``）。若同一指纹已有 queued/processing 任务，或窗口内
   刚创建过作品，就直接返回已有记录（``deduplicated=True``），不再启新任务、
   不重复调用 LLM / 音乐生成接口。前端无感，响应结构与新建完全一致。
2. **并发闸门**：``_create_lock`` 把「查重 + 插入」串行化，杜绝并发双击时
   两个请求同时查不到、各自插一条；deps.py 另有滑动窗口限流与全局/单客户端
   活跃任务上限。

注意：本类不执行五阶段流程，具体编排在 WorkflowService；
生成任务通过构造参数 ``submit_job``（async 回调）注入，单测可换成同步执行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import settings
from ..constants import STAGE_LABELS, STATUS_FAILED, STATUS_QUEUED, USER_INPUT_FILE
from ..dal import ArtifactStore, SongDAL, StageDAL
from ..ids import new_song_id
from ..models import (
    GenerationCreate,
    GenerationDetailResponse,
    GenerationResponse,
    SongRecord,
    StageResponse,
)
from .cover_theme import compute_theme

logger = logging.getLogger(__name__)


def _fallback_title(prompt: str) -> str:
    """用户没填标题时，取描述前 18 字作为临时标题（后续可能被润色阶段覆盖）。"""
    cleaned = " ".join(prompt.split())
    return (cleaned[:18] or "未命名作品").strip()


def request_fingerprint(payload: GenerationCreate, idempotency_key: str | None = None) -> str:
    """计算一次生成请求的稳定指纹，用于幂等去重。

    * 客户端传了 ``Idempotency-Key`` 时以它为准（标准幂等语义，适合重试）；
    * 否则对「真正影响生成结果」的字段做规范化后 sha256：去首尾空白、
      统一布尔值、排除 ``metadata``（透传字段，不参与生成，且常含时间戳，
      纳入会导致每次请求都不同）。

    规范化很重要：用户双击时两次提交的 prompt 可能只差一个换行，仍应命中。
    """
    if idempotency_key and idempotency_key.strip():
        return hashlib.sha256(f"idempotency-key:{idempotency_key.strip()}".encode("utf-8")).hexdigest()

    canonical = {
        "title": (payload.title or "").strip(),
        "task_type": payload.task_type,
        "mode": payload.mode,
        "model_variant": payload.model_variant,
        "inputs": {
            "prompt": (payload.inputs.prompt or "").strip(),
            "lyrics": (payload.inputs.lyrics or "").strip(),
            "instrumental": bool(payload.inputs.instrumental),
            "bpm": payload.inputs.bpm,
            "duration_seconds": payload.inputs.duration_seconds,
        },
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TooManyActiveJobs(Exception):
    """活跃生成任务数超过配置上限；由路由统一转成 HTTP 429。

    service 层不直接依赖 FastAPI，故用业务异常而非 HTTPException。
    """


@dataclass(frozen=True)
class CreateResult:
    """创建结果：响应体 + 是否命中幂等去重。

    路由用 ``deduplicated`` 决定是否加 ``X-Deduplicated: true`` 响应头，
    不改动 GenerationResponse 结构，前端契约保持不变。
    """

    record: GenerationResponse
    deduplicated: bool


class SongService:
    """作品 CRUD + 响应组装（所有公开 IO 方法均为 async）。

    Args:
        submit_job: 接收 song_id 的 async 回调，用于把生成任务交给后台任务。
        config / songs / stages: 均可注入，便于测试隔离数据库。
    """

    def __init__(
        self,
        *,
        submit_job: Callable[[str], Awaitable[None]],
        config: Any = None,
        songs: SongDAL | None = None,
        stages: StageDAL | None = None,
    ) -> None:
        self.config = config or settings
        self.songs = songs or SongDAL()
        self.stages = stages or StageDAL()
        self.submit_job = submit_job
        # 单进程内串行化「查重 + 插入」。asyncio.Lock 在 3.10+ 惰性绑定
        # event loop，只要不同时被两个 loop 使用即可（reset_runtime 会重建）。
        self._create_lock = asyncio.Lock()

    # ---------------- 查询 ----------------

    async def to_response(self, record: SongRecord) -> GenerationResponse:
        """数据库实体 -> 前端响应，是全项目唯一的字段映射处。

        三处需要特别留意：
        * ``musical_key`` 映射成前端的 ``key``；
        * ``audio_url`` 只有在本地音频文件确实存在时才生成，避免前端
          拿到 404 的播放地址；外部已有 URL 时原样透传；
        * ``task_type`` / ``model_variant`` 做白名单兜底，历史脏数据不会
          触发 Pydantic Literal 校验错误导致整个列表接口失败。

        文件存在性检查走 to_thread，避免 stat 阻塞 event loop。
        """
        audio_url: str | None = None
        if record.audio_path:
            exists = await asyncio.to_thread(Path(record.audio_path).is_file)
            if exists:
                audio_url = f"/api/generations/{record.id}/audio"
        if audio_url is None and record.audio_url:
            audio_url = record.audio_url
        return GenerationResponse(
            id=record.id,
            title=record.title,
            task_type=record.task_type if record.task_type in ("text2music", "cover", "repaint") else "text2music",
            mode=record.mode,
            model_variant=record.model_variant if record.model_variant in ("base", "turbo", "shift") else "turbo",
            status=record.status,
            prompt=record.prompt,
            lyrics=record.lyrics,
            instrumental=bool(record.instrumental),
            duration_seconds=record.duration_seconds,
            bpm=record.bpm,
            key=record.musical_key,
            time_signature=record.time_signature,
            audio_url=audio_url,
            cover_color=record.cover_color,
            cover_icon=record.cover_icon,
            cover_image_url=record.cover_image_url,
            error_message=record.error_message,
            metadata=record.metadata,
            created_at=record.created_at,
            updated_at=record.updated_at,
            current_stage=record.current_stage,
            style_prompt=record.style_prompt,
        )

    async def list_history(self, limit: int = 25) -> list[GenerationResponse]:
        """作品库列表（倒序），供 GET /api/history 轮询。"""
        records = await self.songs.list(limit)
        return [await self.to_response(item) for item in records]

    async def get(self, song_id: str) -> SongRecord | None:
        """按 ID 取实体，供音频路由等内部使用；不存在返回 None。"""
        return await self.songs.get(song_id)

    async def get_detail(self, song_id: str) -> GenerationDetailResponse | None:
        """作品详情 = 基础响应 + 带中文标签的五阶段进度。"""
        record = await self.songs.get(song_id)
        if record is None:
            return None
        base, stage_rows = await asyncio.gather(self.to_response(record), self.stages.list(song_id))
        stages = [
            StageResponse(
                stage=item.stage,
                label=STAGE_LABELS.get(item.stage, item.stage),
                status=item.status,
                output_file=item.output_file,
                error=item.error,
                started_at=item.started_at,
                finished_at=item.finished_at,
            )
            for item in stage_rows
        ]
        return GenerationDetailResponse(**base.model_dump(), stages=stages)

    async def active_job_counts(self, client_key: str = "") -> tuple[int, int]:
        """返回 ``(全局活跃任务数, 该客户端活跃任务数)``，供限流依赖判断。"""
        total, mine = await asyncio.gather(
            self.songs.count_active(),
            self.songs.count_active_by_client(client_key) if client_key else asyncio.sleep(0, result=0),
        )
        return int(total), int(mine)

    # ---------------- 写入 ----------------

    async def create(
        self,
        payload: GenerationCreate,
        *,
        client_key: str = "",
        idempotency_key: str | None = None,
        force: bool = False,
    ) -> CreateResult:
        """创建作品并立即返回 ``queued``，不等待生成完成。

        步骤：算指纹 -> 加锁查重 -> 生成 UUIDv7 ID -> 推导封面主题 ->
        写 songs 表 -> 预置五阶段 -> 保存用户原始输入 -> 提交后台任务。
        命中去重时直接返回已有记录，不重复计费。
        ``force=True`` 跳过查重，供用户主动「再生成一次」使用。

        提交失败时不抛异常给前端，而是把作品置为 failed 并附错误信息，
        保证「点了生成却查不到记录」的情况不会出现。
        """
        fingerprint = request_fingerprint(payload, idempotency_key)

        # 锁住整个「查重 + 插入」区间：并发双击时第二个请求会等第一个
        # 插完再查，从而命中刚创建的记录，而不是各自插一条。
        async with self._create_lock:
            if not force:
                since = (
                    datetime.now(timezone.utc) - timedelta(seconds=max(0, self.config.dedupe_window_seconds))
                ).isoformat()
                existing = await self.songs.find_duplicate(fingerprint, since)
                if existing is not None:
                    logger.info("重复提交命中已有作品：%s（指纹 %s…）", existing.id, fingerprint[:8])
                    return CreateResult(await self.to_response(existing), True)

            # 并发闸门必须放在幂等判断之后：命中去重的重试不会新增任务，
            # 不该因为「当前任务已满」被误拦。只有真正要新建时才检查。
            if getattr(self.config, "rate_limit_enabled", True):
                total, mine = await self.active_job_counts(client_key)
                if total >= self.config.max_active_jobs:
                    raise TooManyActiveJobs(
                        f"生成任务已达全局上限（{self.config.max_active_jobs}），请等待现有任务完成"
                    )
                if mine >= self.config.max_active_jobs_per_client:
                    raise TooManyActiveJobs(
                        f"当前客户端排队任务过多（上限 {self.config.max_active_jobs_per_client}）"
                    )

            song_id = new_song_id()  # UUIDv7：时间有序，利于主键索引与作品排序
            prompt = (payload.inputs.prompt or "").strip()
            lyrics = payload.inputs.lyrics or ""
            color, icon = compute_theme(song_id)
            record = SongRecord(
                id=song_id,
                title=(payload.title or "").strip() or _fallback_title(prompt),
                task_type=payload.task_type,
                mode=payload.mode,
                model_variant=payload.model_variant,
                status=STATUS_QUEUED,
                prompt=prompt,
                lyrics=lyrics,
                instrumental=payload.inputs.instrumental,
                duration_seconds=payload.inputs.duration_seconds,
                bpm=payload.inputs.bpm,
                source_prompt=prompt,
                metadata=dict(payload.metadata),
                cover_color=color,
                cover_icon=icon,
                input_fingerprint=fingerprint,
                client_key=client_key or None,
            )
            await self.songs.create(record)
            await self.stages.initialize(song_id)
            store = ArtifactStore(Path(self.config.outputs_dir) / song_id)
            await store.save_text(prompt, USER_INPUT_FILE)

            try:
                await self.submit_job(song_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("后台任务提交失败：%s", song_id)
                await self.songs.update(song_id, status=STATUS_FAILED, error_message=f"任务提交失败：{exc}")
                record.status = STATUS_FAILED
                record.error_message = f"任务提交失败：{exc}"
            return CreateResult(await self.to_response(record), False)

    async def delete(self, song_id: str) -> bool:
        """删除作品：先清输出目录，再删数据库记录（阶段记录靠外键级联）。

        安全约束：仅当 ``task_dir`` 的父目录正是 outputs 根目录时才整目录
        删除，防止 song_id 被构造成 ``..`` 之类路径误删其他目录。
        """
        record = await self.songs.get(song_id)
        if record is None:
            return False
        outputs_root = Path(self.config.outputs_dir).resolve()
        task_dir = (outputs_root / song_id).resolve()
        if task_dir.parent == outputs_root and await asyncio.to_thread(task_dir.is_dir):
            await asyncio.to_thread(shutil.rmtree, task_dir, ignore_errors=True)
        elif record.audio_path:
            await asyncio.to_thread(Path(record.audio_path).unlink, missing_ok=True)
        return await self.songs.delete(song_id)
