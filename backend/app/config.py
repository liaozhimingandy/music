"""运行期配置：集中读取环境变量，不在代码里硬编码任何密钥。

优先级：环境变量 > backend/app/.env > backend/.env > 仓库根 .env > 默认值。
``.env`` 已被 .gitignore 忽略，模板见 backend/app/.env.example。
文件按上面顺序全部加载，靠前的同名变量优先（pydantic-settings 行为）。

维护提示
--------
* 新增配置项时，同步补一份到 ``.env.example``（只写键名和占位值，不写真实密钥）。
* ``validation_alias`` 里保留 ``ACE_STEP_*`` 是为了兼容旧启动脚本，改名时
  记得一起改 scripts/start.bat、start.sh。
* ``settings`` 在模块导入时即实例化并创建数据目录，属于进程级单例。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent          # backend/app
BACKEND_DIR = APP_DIR.parent                      # backend
PROJECT_ROOT = BACKEND_DIR.parent                 # 仓库根

ENV_FILES = tuple(str(path) for path in (APP_DIR / ".env", BACKEND_DIR / ".env", PROJECT_ROOT / ".env"))


class Settings(BaseSettings):
    """所有运行期开关；字段即配置项，缺省值保证「零配置也能跑离线演示」。"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # .env 里的无关变量不报错
    )

    app_name: str = "Music Studio"
    debug: bool = False

    # 网络（沿用旧启动脚本的 ACE_STEP_* 变量名，避免破坏 scripts/start.bat）
    host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("ACE_STEP_HOST", "MUSIC_STUDIO_HOST"),
    )
    port: int = Field(default=8788, validation_alias=AliasChoices("ACE_STEP_PORT", "MUSIC_STUDIO_PORT"))
    # 跨域：前端 dev 直连后端（例如 http://127.0.0.1:5177 -> http://localhost:8000）
    # 是不同源，必须放行。默认允许**任意端口的本机回环来源**，省得每换一个
    # 端口就改配置；生产用 CORS_ORIGINS 显式列出域名，逗号分隔或 JSON 数组均可。
    # 把 MUSIC_STUDIO_CORS_ORIGIN_REGEX 设为空可关闭「回环全放行」。
    cors_origins: str = Field(
        default="",
        validation_alias=AliasChoices("CORS_ORIGINS", "MUSIC_STUDIO_CORS_ORIGINS"),
    )
    cors_origin_regex: str = Field(
        default=r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$",
        validation_alias="MUSIC_STUDIO_CORS_ORIGIN_REGEX",
    )

    @property
    def cors_allow_origins(self) -> list[str]:
        """解析 CORS_ORIGINS；空值表示只靠回环正则放行本机开发来源。"""
        raw = self.cors_origins.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in raw.split(",") if item.strip()]

    # 路径：相对路径一律以 PROJECT_ROOT 为基准解析（见 resolve）。
    data_dir: Path = Field(default=PROJECT_ROOT / "data", validation_alias="MUSIC_STUDIO_DATA_DIR")
    prompts_dir: Path = Field(default=APP_DIR / "prompts", validation_alias="MUSIC_STUDIO_PROMPTS_DIR")

    # LLM（OpenAI 兼容端点）
    llm_base_url: str = Field(default="https://llm.alsoapp.com/v1", validation_alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAIM_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"),
    )
    llm_model: str = Field(default="deepseek-ai/DeepSeek-V4.1-Flash-1017", validation_alias="LLM_MODEL")
    llm_ca_cert: str | None = Field(default=None, validation_alias="LLM_CA_CERT")  # 企业代理自签名证书
    llm_connect_timeout: float = 30.0
    llm_write_timeout: float = 60.0
    llm_read_timeout: float | None = None  # None = 不限制等待模型响应的时间
    llm_max_retries: int = 2

    # 音乐生成（ModelScope ACE-Step 异步推理）
    music_api_base_url: str = Field(
        default="https://api-inference.modelscope.cn", validation_alias="MUSIC_API_BASE_URL"
    )
    music_api_token: str | None = Field(default=None, validation_alias="MUSIC_API_TOKEN")
    # 音乐模型 ID 的唯一来源：MusicProvider 提交、stage_steps 的 target_model
    # 校验、SongService 给 model_variant 兜底都读这里；也由 /v1/health 暴露。
    # prompts/music/system.md 的示例写死了同一个值，换模型前先确认模板兼容。
    music_model: str = Field(default="ACE-Step/acestep-v15-xl-sft", validation_alias="MUSIC_MODEL")
    music_poll_interval: int = 5       # 轮询远端任务状态的间隔（秒）
    music_timeout: int = 1800          # 单次等待生成的总上限（秒）
    music_download_timeout: float = 120.0

    # 流程
    mock_mode: bool = Field(default=False, validation_alias="MUSIC_STUDIO_MOCK")  # 离线演示：不联网、不消耗密钥
    default_duration_seconds: int = 180

    # 接口防护：防重复提交与恶意刷接口（实现见 deps.py / services/rate_limiter.py）
    # 全部有安全缺省值，零配置也能防住双击、脚本循环调用与超大请求体。
    rate_limit_enabled: bool = Field(default=True, validation_alias="MUSIC_STUDIO_RATE_LIMIT_ENABLED")
    rate_limit_window_seconds: int = Field(default=60, ge=1, validation_alias="MUSIC_STUDIO_RATE_LIMIT_WINDOW")
    rate_limit_read: int = Field(default=180, ge=1, validation_alias="MUSIC_STUDIO_RATE_LIMIT_READ")
    rate_limit_llm: int = Field(default=20, ge=1, validation_alias="MUSIC_STUDIO_RATE_LIMIT_LLM")
    rate_limit_create: int = Field(default=5, ge=1, validation_alias="MUSIC_STUDIO_RATE_LIMIT_CREATE")
    max_active_jobs: int = Field(default=4, ge=1, validation_alias="MUSIC_STUDIO_MAX_ACTIVE_JOBS")
    max_active_jobs_per_client: int = Field(default=2, ge=1, validation_alias="MUSIC_STUDIO_MAX_ACTIVE_PER_CLIENT")
    dedupe_window_seconds: int = Field(default=30, ge=0, validation_alias="MUSIC_STUDIO_DEDUPE_WINDOW")
    max_request_bytes: int = Field(default=64 * 1024, ge=1024, validation_alias="MUSIC_STUDIO_MAX_REQUEST_BYTES")
    # 默认不信任 X-Forwarded-For（可伪造）；只有部署在可信反向代理后才开启。
    trust_proxy_headers: bool = Field(default=False, validation_alias="MUSIC_STUDIO_TRUST_PROXY")

    @property
    def db_path(self) -> Path:
        """SQLite 数据库文件路径：``<data_dir>/music_studio.db``。"""
        return self.resolve(self.data_dir) / "music_studio.db"

    @property
    def outputs_dir(self) -> Path:
        """任务产物根目录：``<data_dir>/outputs/<song_id>/``。"""
        return self.resolve(self.data_dir) / "outputs"

    def resolve(self, path: Path) -> Path:
        """把配置里的相对路径统一解析成基于仓库根的绝对路径。"""
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    def ensure_directories(self) -> None:
        """确保数据目录与产物目录存在，避免首次启动时写库失败。"""
        for directory in (self.resolve(self.data_dir), self.resolve(self.outputs_dir)):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()
