"""Music Studio FastAPI 入口。

启动：``uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788``
分层：``routers -> service -> dal -> model``（SQLite3）。

应用生命周期（async lifespan）：
* 启动：``await init_schema()`` 建表 / 补列（幂等）并打印数据库路径；
* 关闭：``await runtime.shutdown()`` 取消后台 asyncio 任务、释放
  httpx 连接池与 Service 单例。

全链路异步：路由 -> service -> dal(aiosqlite) 均无阻塞 IO，等待 LLM /
音乐生成时 event loop 仍可响应前端轮询。接口清单见 docs/api.md。
所有接口统一挂在 ``/v1`` 版本前缀下（见 routers/__init__.py 的 api_v1）。
跨域默认放行本机回环来源，且 500 等错误响应同样带 CORS 头（见 middleware.py）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .dal import close_connection, init_schema
from .middleware import UnhandledErrorMiddleware
from .routers import api_v1
from .services import runtime

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 - FastAPI 约定的签名
    """启动建表、关闭清理；TestClient 进入 with 时也会走这里。"""
    await init_schema()
    logging.getLogger(__name__).info("SQLite 数据库就绪：%s", settings.db_path)
    yield
    # 先取消后台任务（它们可能还要写一次 failed 状态），最后才关数据库连接。
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
)

# 所有业务接口（含 /v1/health）都从 api_v1 聚合路由一次注册；版本前缀只在
# app/constants.py 的 API_V1_PREFIX 定义，子 router 里不再出现任何版本号。
# /docs、/openapi.json 属于 FastAPI 自带的文档端点，不带版本前缀。
app.include_router(api_v1)
