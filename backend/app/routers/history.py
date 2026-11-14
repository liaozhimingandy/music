"""GET /api/history：前端作品库列表（2 秒轮询）。"""

from __future__ import annotations

from fastapi import APIRouter

from ..models import GenerationResponse
from ..services.runtime import get_song_service

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=list[GenerationResponse])
def list_history(limit: int = 25) -> list[GenerationResponse]:
    return get_song_service().list_history(limit)
