"""model 层：表结构、领域实体、API Schema。"""

from .db_schema import SCHEMA_SQL, SONG_COLUMNS, STAGE_COLUMNS
from .entities import SongRecord, StageRecord
from .schemas import (
    GenerationCreate,
    GenerationDetailResponse,
    GenerationInputs,
    GenerationResponse,
    HealthResponse,
    LLMTaskRequest,
    LLMTaskResponse,
    StageResponse,
)

__all__ = [
    "SCHEMA_SQL",
    "SONG_COLUMNS",
    "STAGE_COLUMNS",
    "SongRecord",
    "StageRecord",
    "GenerationInputs",
    "GenerationCreate",
    "GenerationResponse",
    "GenerationDetailResponse",
    "StageResponse",
    "LLMTaskRequest",
    "LLMTaskResponse",
    "HealthResponse",
]
