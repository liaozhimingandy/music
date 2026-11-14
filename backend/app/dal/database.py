"""dal 层：SQLite3 连接管理与建表（全异步、单长连接）。

用 ``aiosqlite`` 把同步的 sqlite3 调用挪到后台线程，所有路由与 service
都跑在 asyncio event loop 上，不会被数据库 IO 阻塞。

为什么用「一条长连接 + 事务锁」而不是「一操作一连接」
--------------------------------------------------
SQLite 只允许一个写者，多连接本来就要排队。更关键的是 WAL 模式下，
**最后一个连接关闭时会删除 -wal / -shm 文件**：每次操作都开关连接，
在 Windows 上并发读写时会出现「新连接正在打开、-shm 却被上一连接删掉」
的竞态，SQLite 随即把该连接判定为只读并抛
``attempt to write a readonly database``（本项目真实踩过，稳定复现在
后台任务第三阶段）。改为进程内复用一条连接后，该问题彻底消失，同时省去
每次建线程/建连接的开销。

``connection()`` 用 ``asyncio.Lock`` 圈住整个事务：yield 期间可能有
``await``（例如 executescript 之后还有补列操作），锁能保证两个请求不会
交错进同一个事务。所有 DAL 方法都通过 ``async with connection()`` 取连接，
**不要在 connection() 内部再嵌套调用它**（会死锁）。

连接按 event loop 缓存：uvicorn 单 loop 长期复用；TestClient 每次 with
会新建 loop，``init_schema()`` 会检测并丢弃旧连接，lifespan 关闭时再
``close_connection()`` 释放。
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from ..config import settings
from ..models import POST_MIGRATION_SQL, SCHEMA_SQL, SONG_TABLE_MIGRATIONS

logger = logging.getLogger(__name__)

_conn: aiosqlite.Connection | None = None
_conn_loop: asyncio.AbstractEventLoop | None = None
_tx_lock: asyncio.Lock | None = None
_tx_lock_loop: asyncio.AbstractEventLoop | None = None


def utc_now() -> str:
    """统一的时间戳格式：ISO 8601 UTC 字符串，供 created_at / updated_at 使用。

    纯计算函数，无 IO，保持同步即可。
    """
    return datetime.now(timezone.utc).isoformat()


def database_path() -> Path:
    """解析数据库文件的绝对路径（配置里的相对路径以仓库根为基准）。"""
    return settings.resolve(settings.db_path)


def _lock() -> asyncio.Lock:
    """取当前 event loop 对应的事务锁（loop 变化时重建）。"""
    global _tx_lock, _tx_lock_loop
    loop = asyncio.get_running_loop()
    if _tx_lock is None or _tx_lock_loop is not loop:
        _tx_lock = asyncio.Lock()
        _tx_lock_loop = loop
    return _tx_lock


async def _drop_stale_connection(loop: asyncio.AbstractEventLoop) -> None:
    """event loop 变了就丢弃旧连接，避免 aiosqlite 的队列跨 loop 失效。

    旧连接属于已关闭的 loop，``close()`` 可能等不到回应，故加超时兜底；
    这只发生在测试反复启停应用的场景，生产单 loop 不会走到这里。
    """
    global _conn, _conn_loop
    if _conn is not None and _conn_loop is not loop:
        old, _conn, _conn_loop = _conn, None, None
        try:
            await asyncio.wait_for(old.close(), timeout=2.0)
        except Exception:  # noqa: BLE001 - 旧 loop 已关闭，尽力而为
            logger.warning("旧数据库连接未能正常关闭（跨 event loop），已丢弃")


async def _get_connection() -> aiosqlite.Connection:
    """惰性创建并复用唯一的异步连接；调用方必须已持有事务锁。"""
    global _conn, _conn_loop
    loop = asyncio.get_running_loop()
    if _conn is not None and _conn_loop is loop:
        return _conn

    await _drop_stale_connection(loop)
    path = database_path()
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    await conn.execute("PRAGMA foreign_keys = ON")   # 让 song_stages 级联删除生效
    await conn.execute("PRAGMA busy_timeout = 30000")
    # WAL 是数据库文件级持久属性，只在这里设置一次；其它连接不再重复设置，
    # 避免与其它事务争抢排他锁（详见模块 docstring）。
    cursor = await conn.execute("PRAGMA journal_mode = WAL")
    await cursor.fetchone()
    await cursor.close()
    await conn.commit()
    _conn, _conn_loop = conn, loop
    return conn


@asynccontextmanager
async def connection() -> AsyncIterator[aiosqlite.Connection]:
    """进入一个受锁保护的数据库事务；正常退出提交，异常回滚。

    连接不在这里关闭（由 ``close_connection()`` 在应用关闭时统一处理）。
    """
    async with _lock():
        conn = await _get_connection()
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise


async def _ensure_song_columns(conn: aiosqlite.Connection) -> None:
    """旧库平滑升级：补齐后来新增的列，再建依赖这些列的索引。

    ``CREATE TABLE IF NOT EXISTS`` 不会修改已有表，所以必须显式 ALTER，
    否则旧 ``data/music_studio.db`` 会在 INSERT 新字段时直接报错。
    列名与类型来自 models.db_schema 的常量白名单（非用户输入），
    故可以用 f-string 拼 DDL。
    """
    cursor = await conn.execute("PRAGMA table_info(songs)")
    rows = await cursor.fetchall()
    await cursor.close()
    existing = {row[1] for row in rows}
    for column, declaration in SONG_TABLE_MIGRATIONS.items():
        if column not in existing:
            logger.info("旧库补列：songs.%s %s", column, declaration)
            await conn.execute(f"ALTER TABLE songs ADD COLUMN {column} {declaration}")


async def init_schema() -> None:
    """应用启动时建表 + 补列（幂等），由 main.py 的 async lifespan 调用。

    顺序很重要：先建表（新库一次到位），再补旧库缺失的列，最后才建依赖
    新列的索引——否则旧库会因为「列不存在」在建索引时失败、导致启动中断。
    """
    async with connection() as conn:
        await conn.executescript(SCHEMA_SQL)
        await _ensure_song_columns(conn)
        await conn.executescript(POST_MIGRATION_SQL)


async def close_connection() -> None:
    """应用关闭时释放长连接；由 main.py 的 async lifespan 调用。

    必须排在 ``runtime.shutdown()`` 之后：被取消的后台任务还要写一次
    failed 状态，提前关库会让那次写入失败。
    """
    global _conn, _conn_loop, _tx_lock, _tx_lock_loop
    if _conn is not None:
        await _conn.close()
    _conn = None
    _conn_loop = None
    _tx_lock = None
    _tx_lock_loop = None
