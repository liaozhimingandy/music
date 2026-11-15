"""健康检查与运行模式信息（GET /v1/health）。

刻意不挂读限流：运维探针 / 负载均衡可能高频调用 /v1/health，被 429 会造成
误判；其余接口的防护由 deps.py 负责。``/v1`` 由聚合路由统一添加。

响应里额外带每日生成配额快照（``daily_*``）：运维一眼能看出「今天还剩几首」，
前端也能在创作面板展示额度，不必再单独请求一个接口。这里只有一次
``COUNT(*)``（走 idx_songs_created_at），对探针频率不构成压力。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import settings
from ..dal import database_path
from ..deps import reject_oversized_body
from ..models import HealthResponse
from ..services.runtime import get_song_service
from ..services.song_service import SongService

router = APIRouter(tags=["meta"], dependencies=[Depends(reject_oversized_body)])


@router.get("/health", response_model=HealthResponse)
async def health(svc: SongService = Depends(get_song_service)) -> HealthResponse:
    """返回应用名、mock 模式、数据库路径、当前音乐模型与今日配额。

    前端顶栏据此显示「后端已连接 / 后端未连接」；运维可用
    ``database`` 确认连的是哪个库文件；``music_model`` 是模型标识的唯一
    权威来源（等价于 GenerationCreate.model_variant 的合法值）。
    ``daily_limit`` 为 0 表示不限额，此时 ``daily_remaining`` 是 None。
    """
    quota = await svc.daily_quota()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        mock_mode=settings.mock_mode,
        database=str(database_path()),
        music_model=settings.music_model,
        daily_limit=quota.limit,
        daily_used=quota.used,
        daily_remaining=quota.remaining,
        daily_reset_at=quota.reset_at.isoformat(),
    )
