"""健康检查与运行模式信息。"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import settings
from ..dal import database_path
from ..models import HealthResponse

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
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
