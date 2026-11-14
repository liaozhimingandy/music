"""运行期配置：集中读取环境变量，不在代码里硬编码任何密钥。

优先级：环境变量 > backend/app/.env > backend/.env > 仓库根 .env > 默认值。
`.env` 已被 .gitignore 忽略，模板见 backend/app/.env.example。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

ENV_FILES = tuple(str(path) for path in (APP_DIR / ".env", BACKEND_DIR / ".env", PROJECT_ROOT / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Music Studio"
    debug: bool = False

    # 网络（沿用旧启动脚本的 ACE_STEP_* 变量名，避免破坏 scripts/start.bat）
    host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("ACE_STEP_HOST", "MUSIC_STUDIO_HOST"),
    )
    port: int = Field(default=8788, validation_alias=AliasChoices("ACE_STEP_PORT", "MUSIC_STUDIO_PORT"))
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5175",
            "http://127.0.0.1:5175",
            "http://localhost:5177",
            "http://127.0.0.1:5177",
        ],
        validation_alias="CORS_ORIGINS",
    )

    # 路径
    data_dir: Path = Field(default=PROJECT_ROOT / "data", validation_alias="MUSIC_STUDIO_DATA_DIR")
    prompts_dir: Path = Field(default=APP_DIR / "prompts", validation_alias="MUSIC_STUDIO_PROMPTS_DIR")

    # LLM（OpenAI 兼容端点）
    llm_base_url: str = Field(default="https://llm.alsoapp.com/v1", validation_alias="LLM_BASE_URL")
    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAIM_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"),
    )
    llm_model: str = Field(default="deepseek-ai/DeepSeek-V4.1-Flash-1017", validation_alias="LLM_MODEL")
    llm_ca_cert: str | None = Field(default=None, validation_alias="LLM_CA_CERT")
    llm_connect_timeout: float = 30.0
    llm_write_timeout: float = 60.0
    llm_read_timeout: float | None = None  # None = 不限制等待模型响应的时间
    llm_max_retries: int = 2

    # 音乐生成（ModelScope ACE-Step 异步推理）
    music_api_base_url: str = Field(
        default="https://api-inference.modelscope.cn", validation_alias="MUSIC_API_BASE_URL"
    )
    music_api_token: str | None = Field(default=None, validation_alias="MUSIC_API_TOKEN")
    music_model: str = Field(default="ACE-Step/acestep-v15-xl-sft", validation_alias="MUSIC_MODEL")
    music_poll_interval: int = 5
    music_timeout: int = 1800
    music_download_timeout: float = 120.0

    # 流程
    mock_mode: bool = Field(default=False, validation_alias="MUSIC_STUDIO_MOCK")
    max_workers: int = 2
    default_duration_seconds: int = 180

    @property
    def db_path(self) -> Path:
        return self.resolve(self.data_dir) / "music_studio.db"

    @property
    def outputs_dir(self) -> Path:
        return self.resolve(self.data_dir) / "outputs"

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    def ensure_directories(self) -> None:
        for directory in (self.resolve(self.data_dir), self.resolve(self.outputs_dir)):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()
