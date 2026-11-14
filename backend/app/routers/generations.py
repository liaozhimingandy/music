"""作品接口：创建（异步排队）、详情、阶段进度、音频下载、删除。"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..models import GenerationCreate, GenerationDetailResponse, GenerationResponse, StageResponse
from ..services.runtime import get_song_service

router = APIRouter(prefix="/api/generations", tags=["generations"])


def _safe_filename(title: str, fallback: str) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff _-]+", "", title, flags=re.UNICODE).strip()
    return (base[:64] or fallback).strip()


@router.post("", response_model=GenerationResponse)
def create_generation(payload: GenerationCreate) -> GenerationResponse:
    """立即返回 queued 记录；五阶段流程在后台线程执行。"""
    return get_song_service().create(payload)


@router.get("/{song_id}", response_model=GenerationDetailResponse)
def get_generation(song_id: str) -> GenerationDetailResponse:
    detail = get_song_service().get_detail(song_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return detail


@router.get("/{song_id}/stages", response_model=list[StageResponse])
def get_stages(song_id: str) -> list[StageResponse]:
    detail = get_song_service().get_detail(song_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="作品不存在")
    return detail.stages


@router.get("/{song_id}/audio")
def download_audio(song_id: str) -> FileResponse:
    record = get_song_service().get(song_id)
    if record is None or not record.audio_path:
        raise HTTPException(status_code=404, detail="音频尚未生成")
    path = Path(record.audio_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    suffix = path.suffix or ".wav"
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    filename = _safe_filename(record.title or "song", song_id) + suffix
    return FileResponse(path, media_type=media_type, filename=filename)


@router.delete("/{song_id}")
def delete_generation(song_id: str) -> dict[str, str]:
    if not get_song_service().delete(song_id):
        raise HTTPException(status_code=404, detail="作品不存在")
    return {"status": "deleted"}
