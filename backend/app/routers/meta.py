"""健康检查与运行模式信息。"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import settings
from ..dal import database_path
from ..models import HealthResponse

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        mock_mode=settings.mock_mode,
        database=str(database_path()),
    )
