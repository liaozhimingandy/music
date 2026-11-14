"""Music Studio FastAPI 入口。

启动：``uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788``
分层：``routers -> service -> dal -> model``（SQLite3）。

应用生命周期（lifespan）：
* 启动：``init_schema()`` 建表（幂等）并打印数据库路径；
* 关闭：``runtime.shutdown()`` 释放生成任务线程池与 Service 单例。

接口清单见 docs/api.md；健康检查 /health 无需数据库即可访问。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .dal import init_schema
from .routers import generations, history, llm, meta
from .services import runtime

logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 - FastAPI 约定的签名
    """启动建表、关闭清理；TestClient 进入 with 时也会走这里。"""
    init_schema()
    logging.getLogger(__name__).info("SQLite 数据库就绪：%s", settings.db_path)
    yield
    runtime.shutdown()


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
