"""model 层：API 请求 / 响应模型，字段与前端 src/interface/index.ts 对齐。

这一层是前后端契约：改字段名或状态取值前，务必同步修改前端 interface 与
services/api.ts。响应模型由 SongService 统一组装，路由不直接返回数据库实体。

安全约定
--------
所有**请求体**都继承 ``StrictRequestModel``（``extra="forbid"``）并给文本
字段设置长度上限。这是防恶意调用的第一道关卡：超长 prompt 会直接被
Pydantic 以 422 拒绝，不会进入 LLM / 音乐生成计费链路；未知字段也被拒绝，
避免有人塞进巨量数据绕过 size 检查。响应模型不受此限制（可扩展字段）。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 枚举值来自前端的下拉项 / 任务类型，勿随意改名。
TaskType = Literal["text2music", "cover", "repaint"]
ModelVariant = Literal["base", "turbo", "shift"]
LLMTask = Literal["prompt", "lyrics", "title"]

# 与前端 CreatePanel.vue 的 500 字限制保持一致；服务端必须再校一次，
# 因为绕过页面直接调接口时前端的 maxlength 不起作用。
MAX_PROMPT_LENGTH = 500
MAX_LYRICS_LENGTH = 8000
MAX_TITLE_LENGTH = 100
MAX_SEED_LENGTH = 1000
MAX_METADATA_CHARS = 4096


class StrictRequestModel(BaseModel):
    """请求体基类：拒绝未知字段，防止夹带超大 / 非预期数据。"""

    model_config = ConfigDict(extra="forbid")


class GenerationInputs(StrictRequestModel):
    """前端「简单创作」提交的三项输入，另带两个可选的音乐参数。

    ``instrumental`` 为真时 lyrics 会被忽略，服务端按纯音乐处理。
    """

    prompt: str | None = Field(default=None, max_length=MAX_PROMPT_LENGTH)  # 用户描述
    lyrics: str | None = Field(default=None, max_length=MAX_LYRICS_LENGTH)  # 用户自填歌词，可为空由 LLM 生成
    instrumental: bool = False
    bpm: int | None = Field(default=None, ge=30, le=300)
    duration_seconds: int | None = Field(default=None, ge=10, le=600)


class GenerationCreate(StrictRequestModel):
    """POST /api/generations 请求体。创建后立刻返回 queued，不等待生成完成。"""

    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)  # 初值；流程后可能被润色结果覆盖
    task_type: TaskType = "text2music"
    mode: str = Field(default="simple", max_length=32)
    model_variant: ModelVariant = "turbo"
    inputs: GenerationInputs = Field(default_factory=GenerationInputs)
    metadata: dict[str, Any] = Field(default_factory=dict)  # 透传的扩展信息

    @field_validator("metadata")
    @classmethod
    def _limit_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """限制透传 metadata 的序列化体积，避免用它绕过请求体大小检查。"""
        if len(json.dumps(value, ensure_ascii=False, default=str)) > MAX_METADATA_CHARS:
            raise ValueError(f"metadata 过大（上限 {MAX_METADATA_CHARS} 字符）")
        return value


class StageResponse(BaseModel):
    """单个阶段的进度，用于 GET /api/generations/{id}/stages 与详情响应。"""

    stage: str
    label: str                        # 中文展示名，来自 constants.STAGE_LABELS
    status: str                       # pending / running / completed / failed
    output_file: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


class GenerationResponse(BaseModel):
    """GET /api/history、POST /api/generations 的作品结构（前端强依赖）。

    ``key`` 对应数据库列 ``musical_key``；``audio_url`` 为相对路径，前端
    直接拼到 <audio src> 上，离线演示模式下由 Vite 代理到后端。
    """

    id: str
    title: str | None = None
    task_type: TaskType = "text2music"
    mode: str = "simple"
    model_variant: ModelVariant = "turbo"
    status: str = "queued"            # queued / processing / ready / failed
    prompt: str | None = None
    lyrics: str | None = None
    instrumental: bool = False
    duration_seconds: int | None = None
    bpm: int | None = None
    key: str | None = None
    time_signature: str | None = None
    audio_url: str | None = None      # 未完成时为 None，前端据此禁用播放
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
    """作品详情 = 作品基础信息 + 五阶段进度列表。"""
    stages: list[StageResponse] = Field(default_factory=list)


class LLMTaskRequest(StrictRequestModel):
    """POST /api/llm 请求体：供「AI 扩写描述 / 生成歌词 / 起标题」三个按钮复用。"""

    task: LLMTask
    seed_prompt: str = Field(..., max_length=MAX_SEED_LENGTH)  # 用户已输入的描述，作为扩写种子
    instrumental: bool = False       # 纯音乐时不会要求生成人声歌词
    style_tags: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=20)


class LLMTaskResponse(BaseModel):
    """POST /api/llm 响应体；前端只取 ``output`` 回填到对应输入框。"""

    task: str
    output: str
    provider: str                    # 便于排查：real / mock 等
    metadata: dict[str, str] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """GET /health 响应体；前端据此显示「后端已连接 / 演示模式」。"""

    status: str
    app: str
    mock_mode: bool
    database: str
