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


def ca_cert_candidates(raw: str | None) -> list[Path]:
    """把 ``LLM_CA_CERT`` 的原始值展开成「按优先级排列」的候选路径。

    为什么要这么麻烦：``.env`` 里写 ``.\\root.crt`` 在 Windows 控制台看起来
    没问题，但它的解析结果**取决于进程的 CWD**——用 ``uvicorn app.main:app``
    从仓库根启动时，``.\\root.crt`` 指向仓库根，而证书其实在 ``backend/app/``
    下；跑到 Linux 容器里反斜杠更会变成文件名字符的一部分，于是 httpx 报
    「找不到证书」。所以这里统一做三件事：

    1. 去引号 / 去首尾空白（``.env`` 里带引号是常见写法）；
    2. 把 ``\\`` 统一换成正斜杠，Windows 风格路径在任何平台都能接受；
    3. 相对路径依次尝试 **app 目录 → backend 目录 → 仓库根 → 进程 CWD**。

    绝对路径只返回自身，交给调用方判断存在性。
    """
    text = (raw or "").strip().strip('"').strip("'").strip()
    if not text:
        return []
    normalized = text.replace("\\", "/")
    path = Path(normalized).expanduser()
    if path.is_absolute():
        return [path]
    return [APP_DIR / normalized, BACKEND_DIR / normalized, PROJECT_ROOT / normalized, Path.cwd() / normalized]


def resolve_ca_cert(raw: str | None) -> Path | None:
    """返回第一个真实存在的 CA 证书文件；一个都不存在时返回 None（回退系统校验）。"""
    for candidate in ca_cert_candidates(raw):
        if candidate.is_file():
            return candidate
    return None


def cert_search_hint(raw: str | None) -> str:
    """把候选路径拼成一行提示，供启动时的告警日志直接打印。"""
    return "、".join(str(item) for item in ca_cert_candidates(raw)) or "（未配置）"


class Settings(BaseSettings):
    """所有运行期开关；字段即配置项，缺省值保证「零配置也能跑离线演示」。"""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # .env 里的无关变量不报错
    )

    app_name: str = "Music Studio"
    # DEBUG 开关同时决定三件事（见 logging_setup.py）：
    #   1) 日志级别：true -> DEBUG（含请求体摘要、模型返回内容前若干字），false -> INFO；
    #   2) 异常堆栈是否带局部变量（backtrace / diagnose，可能含密钥，生产别开）；
    #   3) FastAPI 的 debug 模式（错误响应里带堆栈）。
    # 环境变量名就是 DEBUG，值接受 true/false/1/0/yes/no（大小写不限）。
    debug: bool = Field(default=False, validation_alias=AliasChoices("DEBUG", "MUSIC_STUDIO_DEBUG"))

    # 日志落盘（loguru）：默认写 <data_dir>/logs/music-studio-YYYY-MM-DD.log，
    # 每天零点轮转、只保留最近 N 天。容器部署挂载数据卷即随数据一起持久化。
    log_file_enabled: bool = Field(default=True, validation_alias="MUSIC_STUDIO_LOG_FILE")
    log_retention_days: int = Field(default=7, ge=1, validation_alias="MUSIC_STUDIO_LOG_RETENTION_DAYS")
    log_dir_override: Path | None = Field(default=None, validation_alias="MUSIC_STUDIO_LOG_DIR")

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
    # 每日生成配额（成本闸门）：每次生成都会真实消耗 LLM 与音乐生成额度，
    # 公开演示环境需要一个「总量上限」兜底。0 表示不限制。
    # 计数口径见 services/quota.py：按 songs 表的创建时间落在「自然日」内统计，
    # 日界默认东八区（daily_reset_utc_offset=8），服务器跑 UTC 也不会错位。
    # 与 max_active_jobs 的区别：那个限制「同时跑几个」，这个限制「一天跑几首」。
    daily_generation_limit: int = Field(
        default=50, ge=0, validation_alias="MUSIC_STUDIO_DAILY_LIMIT"
    )
    daily_reset_utc_offset: float = Field(
        default=8.0, ge=-12, le=14, validation_alias="MUSIC_STUDIO_DAILY_RESET_UTC_OFFSET"
    )
    dedupe_window_seconds: int = Field(default=30, ge=0, validation_alias="MUSIC_STUDIO_DEDUPE_WINDOW")
    max_request_bytes: int = Field(default=64 * 1024, ge=1024, validation_alias="MUSIC_STUDIO_MAX_REQUEST_BYTES")
    # 默认不信任 X-Forwarded-For（可伪造）；只有部署在可信反向代理后才开启。
    trust_proxy_headers: bool = Field(default=False, validation_alias="MUSIC_STUDIO_TRUST_PROXY")

    # 持久化任务队列：任务状态存 SQLite，进程重启后由 worker 扫描恢复。
    # queue_poll_seconds 是巡检间隔（启动时会立刻额外扫一次）；
    # max_recovery_attempts 限制同一个任务被恢复的次数，0 表示禁用自动恢复，
    # 防止反复崩溃的任务无限重跑消耗 LLM / 音乐生成额度。
    queue_poll_seconds: float = Field(
        default=3.0, ge=0.5, validation_alias="MUSIC_STUDIO_QUEUE_POLL_SECONDS"
    )
    max_recovery_attempts: int = Field(
        default=5, ge=0, validation_alias="MUSIC_STUDIO_MAX_RECOVERY_ATTEMPTS"
    )
    # 断点续跑：阶段产物 JSON 存在即跳过该阶段（进程重启后接着跑的关键）。
    # 例外场景是「换了 LLM 模型想重跑」：旧产物不会重生成，歌曲听起来还是老模型的。
    # 想强制用当前模型重跑，把 MUSIC_STUDIO_RESUME 设为 false 即可（产物会被覆盖，
    # 队列里的任务也会从头再跑一遍，音乐与 LLM 额度都会重新消耗）。
    resume_enabled: bool = Field(default=True, validation_alias="MUSIC_STUDIO_RESUME")

    @property
    def logs_dir(self) -> Path:
        """日志目录：``MUSIC_STUDIO_LOG_DIR`` 优先，否则 ``<data_dir>/logs``。"""
        if self.log_dir_override is not None:
            return self.resolve(self.log_dir_override)
        return self.resolve(self.data_dir) / "logs"

    @property
    def llm_ca_cert_path(self) -> Path | None:
        """解析后的 CA 证书绝对路径；未配置或文件不存在时为 None。

        LLMClient 用它构造 ``httpx`` 的 ``verify`` 参数。**不要**在别处直接
        用 ``settings.llm_ca_cert`` 当路径——那是 .env 里的原始字符串，
        相对路径的含义随启动目录变化。
        """
        return resolve_ca_cert(self.llm_ca_cert)

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
        """确保数据目录、产物目录与日志目录存在，避免首次启动时写库/写日志失败。"""
        for directory in (self.resolve(self.data_dir), self.resolve(self.outputs_dir), self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_directories()
