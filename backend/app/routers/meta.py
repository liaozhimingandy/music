"""健康检查与运行模式信息。

刻意不挂读限流：运维探针 / 负载均衡可能高频调用 /health，被 429 会造成
误判；其余接口的防护由 deps.py 负责。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import settings
from ..dal import database_path
from ..deps import reject_oversized_body
from ..models import HealthResponse

router = APIRouter(tags=["meta"], dependencies=[Depends(reject_oversized_body)])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """返回应用名、mock 模式与数据库路径。

    前端顶栏用 ``mock_mode`` 显示「后端已连接 / 演示模式」；运维可用
    ``database`` 快速确认当前连的是哪个库文件。
    """
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        mock_mode=settings.mock_mode,
        database=str(database_path()),
    )
