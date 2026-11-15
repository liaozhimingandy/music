"""Music Studio FastAPI 入口。

启动：
* 本机：``uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788``
  （地址端口由 backend/.env 的 HOST / PORT 决定）
* 容器：``fastapi run app/main.py --port 8000``（FastAPI 官方推荐写法，
  见 backend/Dockerfile）
分层：``routers -> service -> dal -> model``（SQLite3）。

应用生命周期（async lifespan）：
* 启动：``await init_schema()`` 建表 / 补列（幂等），随后
  ``await runtime.start_queue_worker()`` 立即扫描 DB 里上次进程遗留的
  queued/processing 任务重新入队，并挂上周期性巡检；
* 关闭：先停巡检并 cancel 内存里的 task（**不改数据库状态**，任务留待
  重启续跑），再释放 httpx 连接池与 Service 单例，最后关数据库连接。

全链路异步：路由 -> service -> dal(aiosqlite) 均无阻塞 IO，等待 LLM /
音乐生成时 event loop 仍可响应前端轮询。接口清单见 docs/api.md。
所有接口统一挂在 ``/v1`` 版本前缀下（见 routers/__init__.py 的 api_v1）。
跨域默认放行本机回环来源，且 500 等错误响应同样带 CORS 头（见 middleware.py）。
生成任务以 SQLite 为持久化队列，进程重启不丢任务（见 services/runtime.py）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .dal import close_connection, init_schema
from .logging_setup import ensure_loguru_bridge, log_startup_summary, setup_logging
from .middleware import UnhandledErrorMiddleware
from .routers import api_v1
from .services import runtime

# 日志必须在 app 创建前配置好：uvicorn 导入本模块时就会打启动日志。
# 级别由 DEBUG 决定（true -> DEBUG，否则 INFO），文件按天轮转保留 7 天，
# 细节见 logging_setup.py。注意这里不要再用 logging.basicConfig，
# 否则会和 loguru 的 sink 重复输出。
LOG_DIR = setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 - FastAPI 约定的签名
    """启动建表、恢复队列、关闭清理；TestClient 进入 with 时也会走这里。"""
    # `fastapi run`（FastAPI 官方推荐的容器启动方式）会先 import 本模块，
    # 之后 uvicorn 才用 dictConfig 覆盖 uvicorn.* 的 logger，把上面的转发桥接
    # 冲掉——这里在 lifespan 启动时补装一次，uvicorn 的启动 / access 日志
    # 就也能落进 loguru 文件了。详见 logging_setup.ensure_loguru_bridge。
    ensure_loguru_bridge()
    await init_schema()
    # 第一行就打「进程内真实生效的配置」，排查「改了 .env 没生效」先看它。
    log_startup_summary()
    logger.info("SQLite 数据库就绪：%s", settings.db_path)
    logger.info("日志落盘目录：%s（保留 %s 天）", LOG_DIR or "已禁用", settings.log_retention_days)
    await runtime.start_queue_worker()
    yield
    # 先停巡检 / 取消后台任务（保留 DB 状态供下次重启续跑），最后才关连接。
    await runtime.shutdown()
    await close_connection()


app = FastAPI(title=settings.app_name, version="0.1.0", debug=settings.debug, lifespan=lifespan)

# 跨域：前端直连后端（如 5177 -> 8000）时是不同源，默认放行所有本机回环来源，
# 生产用 CORS_ORIGINS 显式列域名。注册顺序有讲究：add_middleware 后加的在外层，
# 所以先挂「异常兜底」（内层）、再挂 CORS（外层），让 500 等错误响应也能补上
# Access-Control-Allow-Origin，避免后端异常被浏览器误判成跨域问题。
app.add_middleware(UnhandledErrorMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 跨域时浏览器默认只把「简单响应头」暴露给 JS，自定义头必须显式放行：
    # 前端要在创作面板显示「今日还剩 X 首」就要读 X-Daily-Remaining，
    # 幂等去重标记 X-Deduplicated 也一样（同源部署无此限制，dev 直连才有）。
    expose_headers=[
        "X-Daily-Limit",
        "X-Daily-Remaining",
        "X-Daily-Reset",
        "X-Deduplicated",
        "Retry-After",
    ],
)

# 所有业务接口（含 /v1/health）都从 api_v1 聚合路由一次注册；版本前缀只在
# app/constants.py 的 API_V1_PREFIX 定义，子 router 里不再出现任何版本号。
# /docs、/openapi.json 属于 FastAPI 自带的文档端点，不带版本前缀。
app.include_router(api_v1)
