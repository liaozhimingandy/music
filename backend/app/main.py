"""Music Studio FastAPI 入口。

启动：``uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788``
分层：``routers -> service -> dal -> model``（SQLite3）。

应用生命周期（async lifespan）：
* 启动：``await init_schema()`` 建表 / 补列（幂等）并打印数据库路径；
* 关闭：``await runtime.shutdown()`` 取消后台 asyncio 任务、释放
  httpx 连接池与 Service 单例。

全链路异步：路由 -> service -> dal(aiosqlite) 均无阻塞 IO，等待 LLM /
音乐生成时 event loop 仍可响应前端轮询。接口清单见 docs/api.md。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .dal import close_connection, init_schema
from .routers import generations, history, llm, meta
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

# 前端默认走 Vite 的 /api 代理（同源），这里主要照顾直连 / Pages 预览的场景。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册顺序不影响匹配；每个 router 自带 prefix。
app.include_router(meta.router)          # /health
app.include_router(history.router)       # /api/history
app.include_router(generations.router)   # /api/generations/*
app.include_router(llm.router)           # /api/llm
