"""GET /api/history：前端作品库列表（2 秒轮询）。"""

from __future__ import annotations

from fastapi import APIRouter

from ..models import GenerationResponse
from ..services.runtime import get_song_service

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=list[GenerationResponse])
def list_history(limit: int = 25) -> list[GenerationResponse]:
    """按创建时间倒序返回作品；``limit`` 会被 SongDAL 钳制在 1~200。

    这是前端判断「后端是否在线」的接口：请求失败且属于连接错误时，
    frontend/src/services/api.ts 会回退到离线演示数据。
    """
    return get_song_service().list_history(limit)
