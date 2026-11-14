"""model 层：API 请求 / 响应模型，字段与前端 src/interface/index.ts 对齐。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TaskType = Literal["text2music", "cover", "repaint"]
ModelVariant = Literal["base", "turbo", "shift"]
LLMTask = Literal["prompt", "lyrics", "title"]


class GenerationInputs(BaseModel):
    """前端「简单创作」提交的三项输入。"""

    prompt: str | None = None
    lyrics: str | None = None
    instrumental: bool = False
    bpm: int | None = Field(default=None, ge=30, le=300)
    duration_seconds: int | None = Field(default=None, ge=10, le=600)


class GenerationCreate(BaseModel):
    title: str | None = None
    task_type: TaskType = "text2music"
    mode: str = "simple"
    model_variant: ModelVariant = "turbo"
    inputs: GenerationInputs = Field(default_factory=GenerationInputs)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StageResponse(BaseModel):
    stage: str
    label: str
    status: str
    output_file: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class GenerationResponse(BaseModel):
    """GET /api/history、POST /api/generations 的作品结构（前端强依赖）。"""

    id: str
    title: str | None = None
    task_type: TaskType = "text2music"
    mode: str = "simple"
    model_variant: ModelVariant = "turbo"
    status: str = "queued"
    prompt: str | None = None
    lyrics: str | None = None
    instrumental: bool = False
    duration_seconds: int | None = None
    bpm: int | None = None
    key: str | None = None
    time_signature: str | None = None
    audio_url: str | None = None
    cover_color: str | None = None
    cover_icon: str | None = None
    cover_image_url: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    # 前端可忽略的附加字段：轮询时用于显示当前阶段
    current_stage: str | None = None
    style_prompt: str | None = None


class GenerationDetailResponse(GenerationResponse):
    stages: list[StageResponse] = Field(default_factory=list)


class LLMTaskRequest(BaseModel):
    task: LLMTask
    seed_prompt: str
    instrumental: bool = False
    style_tags: list[str] = Field(default_factory=list)


class LLMTaskResponse(BaseModel):
    task: str
    output: str
    provider: str
    metadata: dict[str, str] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    app: str
    mock_mode: bool
    database: str
