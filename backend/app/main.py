"""Music Studio FastAPI 入口。

启动：uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8788
分层：routers -> service -> dal -> model（SQLite3）
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
    init_schema()
    logging.getLogger(__name__).info("SQLite 数据库就绪：%s", settings.db_path)
    yield
    runtime.shutdown()


app = FastAPI(title=settings.app_name, version="0.1.0", debug=settings.debug, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(history.router)
app.include_router(generations.router)
app.include_router(llm.router)
