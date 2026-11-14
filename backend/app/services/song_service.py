"""service 层：面向 API 的作品服务。

职责：把前端 GenerationCreate 转成数据库记录 -> 启动后台生成任务 ->
把 SongRecord 映射成前端需要的 GenerationResponse。
这是「前端契约」与「数据库实体」之间唯一的转换点，改字段优先改这里。

注意：本类不执行五阶段流程，具体编排在 WorkflowService；
生成任务通过构造参数 ``submit_job`` 注入，单测可换成同步执行。
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable

from ..config import settings
from ..constants import STAGE_LABELS, STATUS_FAILED, STATUS_QUEUED, USER_INPUT_FILE
from ..dal import ArtifactStore, SongDAL, StageDAL
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


class SongService:
    """作品 CRUD + 响应组装。

    Args:
        submit_job: 接收 song_id 的回调，用于把生成任务丢进后台线程池。
        config / songs / stages: 均可注入，便于测试隔离数据库。
    """

    def __init__(
        self,
        *,
        submit_job: Callable[[str], None],
        config: Any = None,
        songs: SongDAL | None = None,
        stages: StageDAL | None = None,
    ) -> None:
        self.config = config or settings
        self.songs = songs or SongDAL()
        self.stages = stages or StageDAL()
        self.submit_job = submit_job

    # ---------------- 查询 ----------------

    def to_response(self, record: SongRecord) -> GenerationResponse:
        """数据库实体 -> 前端响应，是全项目唯一的字段映射处。

        三处需要特别留意：
        * ``musical_key`` 映射成前端的 ``key``；
        * ``audio_url`` 只有在本地音频文件确实存在时才生成，避免前端
          拿到 404 的播放地址；外部已有 URL 时原样透传；
        * ``task_type`` / ``model_variant`` 做白名单兜底，历史脏数据不会
          触发 Pydantic Literal 校验错误导致整个列表接口失败。
        """
        audio_url: str | None = None
        if record.audio_path and Path(record.audio_path).is_file():
            audio_url = f"/api/generations/{record.id}/audio"
        elif record.audio_url:
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

    def list_history(self, limit: int = 25) -> list[GenerationResponse]:
        """作品库列表（倒序），供 GET /api/history 轮询。"""
        return [self.to_response(item) for item in self.songs.list(limit)]

    def get(self, song_id: str) -> SongRecord | None:
        """按 ID 取实体，供音频路由等内部使用；不存在返回 None。"""
        return self.songs.get(song_id)

    def get_detail(self, song_id: str) -> GenerationDetailResponse | None:
        """作品详情 = 基础响应 + 带中文标签的五阶段进度。"""
        record = self.songs.get(song_id)
        if record is None:
            return None
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
            for item in self.stages.list(song_id)
        ]
        return GenerationDetailResponse(**self.to_response(record).model_dump(), stages=stages)

    # ---------------- 写入 ----------------

    def create(self, payload: GenerationCreate) -> GenerationResponse:
        """创建作品并立即返回 ``queued``，不等待生成完成。

        步骤：生成 ID -> 推导封面主题 -> 写 songs 表 -> 预置五阶段 ->
        保存用户原始输入（阶段一要用）-> 提交后台任务。
        提交失败时不抛异常给前端，而是把作品置为 failed 并附错误信息，
        保证「点了生成却查不到记录」的情况不会出现。
        """
        song_id = uuid.uuid4().hex
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
        )
        self.songs.create(record)
        self.stages.initialize(song_id)
        ArtifactStore(Path(self.config.outputs_dir) / song_id).save_text(prompt, USER_INPUT_FILE)

        try:
            self.submit_job(song_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("后台任务提交失败：%s", song_id)
            self.songs.update(song_id, status=STATUS_FAILED, error_message=f"任务提交失败：{exc}")
            record.status = STATUS_FAILED
            record.error_message = f"任务提交失败：{exc}"
        return self.to_response(record)

    def delete(self, song_id: str) -> bool:
        """删除作品：先清输出目录，再删数据库记录（阶段记录靠外键级联）。

        安全约束：仅当 ``task_dir`` 的父目录正是 outputs 根目录时才整目录
        删除，防止 song_id 被构造成 ``..`` 之类路径误删其他目录。
        """
        record = self.songs.get(song_id)
        if record is None:
            return False
        outputs_root = Path(self.config.outputs_dir).resolve()
        task_dir = (outputs_root / song_id).resolve()
        if task_dir.parent == outputs_root and task_dir.is_dir():
            shutil.rmtree(task_dir, ignore_errors=True)
        elif record.audio_path:
            Path(record.audio_path).unlink(missing_ok=True)
        return self.songs.delete(song_id)
