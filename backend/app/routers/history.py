"""GET /api/history：前端作品库列表（2 秒轮询）。

示例写法：服务通过 ``Depends(get_song_service)`` 注入，路由函数体只用
``svc``，不再直接调用 getter —— 便于单测用 ``app.dependency_overrides``
替换成内存实现，也让 FastAPI 在 OpenAPI 里正确记录依赖关系。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..deps import enforce_read_policy, reject_oversized_body
from ..models import GenerationResponse
from ..services.runtime import get_song_service
from ..services.song_service import SongService

router = APIRouter(
    prefix="/api/history",
    tags=["history"],
    # router 级依赖：所有请求先验体积、再验读限流。
    dependencies=[Depends(reject_oversized_body), Depends(enforce_read_policy)],
)


@router.get("", response_model=list[GenerationResponse])
async def list_history(
    limit: int = Query(default=25, ge=1, le=100, description="返回条数（1~100）"),
    svc: SongService = Depends(get_song_service),
) -> list[GenerationResponse]:
    """按创建时间倒序返回作品；``limit`` 由 Query 与 SongDAL 双重钳制。

    这是前端判断「后端是否在线」的接口：请求失败且属于连接错误时，
    frontend/src/services/api.ts 会回退到离线演示数据。
    """
    return await svc.list_history(limit)
