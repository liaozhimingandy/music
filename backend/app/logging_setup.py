"""运行日志配置：loguru 统一收口（控制台 + 按天轮转文件，默认保留 7 天）。

为什么单独一个模块
------------------
原来 ``main.py`` 里只有一行 ``logging.basicConfig``：没有文件落盘、没有轮转、
DEBUG 开关只影响级别，容器里 ``docker logs`` 一滚历史就没了。排查「改了模型
但数据库没记录」这类问题时，最需要的是 **当时那次请求用了什么模型、返回了
什么**，所以这里把日志统一到 loguru：

* 控制台：开发时直接看，带颜色；INFO 模式一行一条，DEBUG 模式多带
  ``模块:函数:行号`` 与异常堆栈（backtrace / diagnose）。
* 文件：``<data_dir>/logs/music-studio-YYYY-MM-DD.log``，UTF-8，按天轮转
  （零点切分），``retention`` 自动清理超过保留天数的旧文件。
  放在 data 目录下，容器里挂载数据卷即可持久化。
* 级别：``DEBUG=true`` -> DEBUG，否则 INFO（见 config.Settings.debug，
  环境变量名就是 ``DEBUG``）。

存量代码用的是标准库 ``logging``（``logger = logging.getLogger(__name__)``），
全部改一遍既无必要也容易出错，所以这里装一个 ``InterceptHandler`` 把标准库
的 record 转发给 loguru——两套 API 混用也能落到同一个文件里。

维护提示
--------
* 新增模块照旧用 ``logging.getLogger(__name__)`` 即可，无需 import loguru。
* 不要在别的模块里 ``logger.add(...)``，所有 sink 只在这个文件里注册。
* 日志文件不要提交：``data/`` 已在 .gitignore 中。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from .config import settings

# 控制台格式：INFO 模式尽量短（一行看清「谁 + 干了什么」）；DEBUG 模式补上
# 函数名与行号，方便直接定位代码。
CONSOLE_FORMAT_INFO = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
    "<cyan>{name}</cyan> - <level>{message}</level>"
)
CONSOLE_FORMAT_DEBUG = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)
# 文件格式固定带完整时间戳与位置：事后排查无法靠「猜是哪个函数打的」。
FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"

# 这些 logger 会被重置为只向 loguru 转发，避免 uvicorn 自带 handler 造成重复输出：
# uvicorn 在 import 时就装好了自己的 StreamHandler，不清掉同一条日志会打印两遍。
_INTERCEPTED_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "fastapi",
    "httpx",
    "httpcore",
    "aiosqlite",
    "asyncio",
)

# 这些库的 DEBUG 是「每执行一条 SQL / 每次事件循环调度都打一行」，即使开了
# DEBUG 也没有排查价值，反而会把真正有用的行淹掉，所以固定压到 INFO。
_QUIET_LOGGERS = ("asyncio", "aiosqlite")


class InterceptHandler(logging.Handler):
    """把标准库 ``logging`` 的记录转发给 loguru。

    与 loguru 官方文档的同名类有一处关键差异：官方版本的 ``emit`` 定义在
    ``logging/__init__.py`` 里，所以「往回走直到离开 logging 包」就够了；
    本类定义在别的模块，必须把**本文件**也当作要跳过的中间层，否则日志位置
    会错误地显示成 ``logging:callHandlers``（看不出是谁打的）。
    """

    _SKIP_FILES = (logging.__file__, __file__)

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102 - 见类文档
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:  # 自定义级别（如 25）不在 loguru 表里，直接用数字
            level = record.levelno

        # depth 从 0 起算、每跳过一层 logging / 本文件的帧就 +1（实测见
        # .probe_depth2.py：官方示例的 depth=2 在本类里会多走一层，日志位置
        # 变成 ``None:<unknown>:0``）。
        frame, depth = logging.currentframe(), 0
        while frame is not None:
            if frame.f_code.co_filename not in self._SKIP_FILES:
                break
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _resolve_log_dir(config: Any) -> Path:
    """日志目录：显式配置优先，否则 ``<data_dir>/logs``。

    直接用 ``config.logs_dir``（Settings 上的 property），自定义配置对象没有
    这个属性时退回到 data_dir 拼接，保证测试注入的假配置也能用。
    """
    explicit = getattr(config, "logs_dir", None)
    if explicit is not None:
        return Path(explicit)
    return Path(getattr(config, "data_dir", "data")) / "logs"


def setup_logging(config: Any = None) -> Path | None:
    """初始化全局日志，返回日志目录（文件日志关闭时返回 None）。

    可重复调用（测试里多次建 app 不会叠加 sink）：先 ``logger.remove()``
    清空已有 sink 再重新注册。

    Args:
        config: 可注入的配置对象；缺省使用全局 ``settings``。

    Returns:
        日志文件所在目录；``MUSIC_STUDIO_LOG_FILE=false`` 时为 None。
    """
    cfg = config or settings
    debug = bool(getattr(cfg, "debug", False))
    level = "DEBUG" if debug else "INFO"

    logger.remove()

    # 控制台：容器里 docker logs 直接可读。backtrace/diagnose 只在 DEBUG 开，
    # 生产环境它会把局部变量打进日志（可能含 API Key），默认关闭更安全。
    logger.add(
        sys.stderr,
        level=level,
        format=CONSOLE_FORMAT_DEBUG if debug else CONSOLE_FORMAT_INFO,
        colorize=True,
        backtrace=debug,
        diagnose=debug,
        enqueue=False,
    )

    log_dir: Path | None = None
    if bool(getattr(cfg, "log_file_enabled", True)):
        log_dir = _resolve_log_dir(cfg)
        log_dir.mkdir(parents=True, exist_ok=True)
        retention_days = int(getattr(cfg, "log_retention_days", 7) or 7)
        logger.add(
            str(log_dir / "music-studio-{time:YYYY-MM-DD}.log"),
            level=level,
            format=FILE_FORMAT,
            rotation="00:00",          # 每天零点切新文件，文件名即日期
            retention=f"{retention_days} days",  # 只留最近 N 天，自动删除更早的
            encoding="utf-8",          # 中文日志在 Windows 下不乱码
            backtrace=debug,
            diagnose=debug,
            enqueue=False,             # 单进程异步应用，同步写更可控、不丢尾部日志
        )

    # 标准库 -> loguru：force=True 清掉可能已存在的 root handler。
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in _INTERCEPTED_LOGGERS:
        std_logger = logging.getLogger(name)
        std_logger.handlers = [InterceptHandler()]
        std_logger.propagate = False
        if name in ("httpx", "httpcore"):
            # httpx 的 INFO 是「每发一个请求打一行」，INFO 模式下太吵；
            # 排查网络问题时把 DEBUG 打开即可看到完整请求日志。
            std_logger.setLevel(logging.DEBUG if debug else logging.WARNING)
        elif name in _QUIET_LOGGERS:
            std_logger.setLevel(logging.INFO)  # 恒定压噪音，不随 DEBUG 放宽
        else:
            std_logger.setLevel(logging.DEBUG if debug else logging.INFO)

    return log_dir


def log_startup_summary(config: Any = None) -> None:
    """打印一行「本次启动实际生效的关键配置」。

    排查「改了 .env 没生效」时，第一眼看的就是这行：它显示的是进程内真实
    读到的值，而不是 .env 文件里写了什么。
    """
    cfg = config or settings
    logger.info(
        "配置生效：LLM 模型={} ｜ 音乐模型={} ｜ DEBUG={} ｜ 断点续跑={} ｜ 每日上限={}",
        getattr(cfg, "llm_model", "-"),
        getattr(cfg, "music_model", "-"),
        bool(getattr(cfg, "debug", False)),
        bool(getattr(cfg, "resume_enabled", True)),
        getattr(cfg, "daily_generation_limit", "-"),
    )
