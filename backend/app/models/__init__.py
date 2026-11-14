"""model 层：表结构、领域实体、API Schema（最内层，不依赖 dal / services）。

分层依赖方向（只允许单向）：
    routers -> services -> dal -> models
    config / constants 为全局横切模块，任何层都可只读引用。

新增字段 Checklist：db_schema.SCHEMA_SQL + SONG_COLUMNS / STAGE_COLUMNS
-> entities 实体字段 -> schemas 响应模型 -> SongService 组装逻辑。
"""

from .db_schema import POST_MIGRATION_SQL, SCHEMA_SQL, SONG_COLUMNS, SONG_TABLE_MIGRATIONS, STAGE_COLUMNS
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
    "POST_MIGRATION_SQL",
    "SONG_TABLE_MIGRATIONS",
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
