"""service 层：面向 API 的作品服务。

负责把前端 GenerationCreate 转成数据库记录、启动后台生成任务、
把 SongRecord 映射成前端需要的 GenerationResponse。
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
    cleaned = " ".join(prompt.split())
    return (cleaned[:18] or "未命名作品").strip()


class SongService:
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
        return [self.to_response(item) for item in self.songs.list(limit)]

    def get(self, song_id: str) -> SongRecord | None:
        return self.songs.get(song_id)

    def get_detail(self, song_id: str) -> GenerationDetailResponse | None:
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
