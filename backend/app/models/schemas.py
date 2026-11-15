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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 枚举值来自前端的任务类型，勿随意改名。
TaskType = Literal["text2music",]
LLMTask = Literal["prompt", "lyrics", "title"]

# 音乐模型 ID 不在这里定义枚举：唯一来源是 config.music_model（MUSIC_MODEL
# 环境变量，默认 ACE-Step/acestep-v15-xl-sft），它既是 MusicProvider 提交给
# ModelScope 的 model 参数，也是 prompts/music/system.md 里写死的 target_model。
# 前端不传 model_variant 时由 SongService 用配置兜底，避免两边各写一份硬编码。

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
    """POST /v1/generations 请求体。创建后立刻返回 queued，不等待生成完成。"""

    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)  # 初值；流程后可能被润色结果覆盖
    task_type: TaskType = "text2music"
    mode: str = Field(default="simple", max_length=32)
    # 可选：仅作展示标记，真正生效的模型由 settings.music_model 决定。
    # 留空时 SongService 会用配置值回填；请求侧不写死 Literal，换模型无需改契约。
    model_variant: str | None = Field(default=None, max_length=128)
    inputs: GenerationInputs = Field(default_factory=GenerationInputs)
    metadata: dict[str, Any] = Field(default_factory=dict)  # 透传的扩展信息

    @model_validator(mode="after")
    def _require_prompt(self) -> GenerationCreate:
        """描述必填：前端已禁用空输入，这里挡住绕过页面直接 POST 的调用。

        放在 GenerationCreate 而不是 GenerationInputs 上，才能覆盖「完全不传
        inputs」时 default_factory 的产物。不提前拦的话空描述会一路走到后台
        planning 阶段才报错，白占一个活跃任务名额并留下一条 failed 作品。
        """
        if not (self.inputs.prompt or "").strip():
            raise ValueError("歌词 / 歌曲描述不能为空")
        return self

    @field_validator("metadata")
    @classmethod
    def _limit_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """限制透传 metadata 的序列化体积，避免用它绕过请求体大小检查。"""
        if len(json.dumps(value, ensure_ascii=False, default=str)) > MAX_METADATA_CHARS:
            raise ValueError(f"metadata 过大（上限 {MAX_METADATA_CHARS} 字符）")
        return value


class StageResponse(BaseModel):
    """单个阶段的进度，用于 GET /v1/generations/{id}/stages 与详情响应。

    遥测字段（``model`` / ``usage`` / ``metrics``）在阶段完成时由后台流程
    写入：``usage`` 为 OpenAI 风格 token 用量（音乐阶段与 mock 客户端为
    None）；``metrics`` 至少含 ``elapsed_ms``，其余键随阶段不同（如远端
    task_id），排查问题直接看这两个字段即可。
    """

    stage: str
    label: str                        # 中文展示名，来自 constants.STAGE_LABELS
    status: str                       # pending / running / completed / failed
    output_file: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    # --- 阶段遥测 ---
    model: str | None = None
    usage: dict[str, Any] | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class GenerationResponse(BaseModel):
    """GET /v1/history、POST /v1/generations 的作品结构（前端强依赖）。

    ``key`` 对应数据库列 ``musical_key``；``audio_url`` 为相对路径，前端
    直接拼到 <audio src> 上，开发环境由 Vite 代理到后端。
    """

    id: str
    title: str | None = None
    task_type: TaskType = "text2music"
    mode: str = "simple"
    # 响应侧固定用 str：songs 表里既有历史值（base / turbo / shift），也有
    # settings.music_model 配置的真实模型 ID。若这里用严格 Literal，任何一条
    # 旧记录都会让整个 /v1/history 抛 ValidationError -> 500（浏览器会误报成
    # 跨域错误）。换个 MUSIC_MODEL 也不需要改这里。
    model_variant: str = "turbo"
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
    """POST /v1/llm 请求体：供「AI 扩写描述 / 生成歌词 / 起标题」三个按钮复用。"""

    task: LLMTask
    seed_prompt: str = Field(..., max_length=MAX_SEED_LENGTH)  # 用户已输入的描述，作为扩写种子
    instrumental: bool = False       # 纯音乐时不会要求生成人声歌词
    style_tags: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=20)


class LLMTaskResponse(BaseModel):
    """POST /v1/llm 响应体；前端只取 ``output`` 回填到对应输入框。"""

    task: str
    output: str
    provider: str                    # 便于排查：real / mock 等
    metadata: dict[str, str] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """GET /v1/health 响应体；前端据此显示「后端已连接 / 后端未连接」。

    ``music_model`` 是当前实际生效的音乐模型 ID（来自 MUSIC_MODEL 配置），
    也是 ``GenerationCreate.model_variant`` 唯一合法的取值来源——前端想查
    「有哪些模型」读这里即可，不必自己维护一份枚举。

    ``daily_*`` 是每日生成配额快照（见 services/quota.py）：``daily_limit``
    为 0 表示不限额，此时 ``daily_remaining`` 返回 None；前端只在它是数字时
    显示「今日还剩 X 首」。
    """

    status: str
    app: str
    mock_mode: bool
    database: str
    music_model: str | None = None
    daily_limit: int | None = None
    daily_used: int | None = None
    daily_remaining: int | None = None
    daily_reset_at: str | None = None
