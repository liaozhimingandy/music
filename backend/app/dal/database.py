"""dal 层：SQLite3 连接管理与建表。

每次操作单独开连接（线程安全），WAL 模式支持后台生成线程与 API 读并发。
连接是短生命周期的：``with connection() as conn`` 结束即提交并关闭，
不要跨请求缓存 Connection 对象。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..config import settings
from ..models import SCHEMA_SQL


def utc_now() -> str:
    """统一的时间戳格式：ISO 8601 UTC 字符串，供 created_at / updated_at 使用。"""
    return datetime.now(timezone.utc).isoformat()


def database_path() -> Path:
    """解析数据库文件的绝对路径（配置里的相对路径以仓库根为基准）。"""
    return settings.resolve(settings.db_path)


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """打开一次带事务的连接；正常退出提交，异常回滚，最终一定关闭。

    三个 PRAGMA 的作用：
    * ``foreign_keys``：开启后才能触发 song_stages 的 ON DELETE CASCADE；
    * ``journal_mode=WAL``：后台线程写阶段进度时，前端轮询读不会被阻塞；
    * ``busy_timeout``：与 connect(timeout=30) 一起避免并发写偶发 "database is locked"。

    ``row_factory`` 设为 sqlite3.Row，让 DAL 可以直接用 ``dict(row)`` 还原实体。
    ``check_same_thread=False`` 仅表示连接不绑定创建线程，实际仍是「一操作一连接」。
    """
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        yield conn
        conn.commit()  # 所有写操作在离开 with 时统一提交
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    """应用启动时建表（幂等）。

    DDL 全部使用 ``IF NOT EXISTS``，因此不会覆盖已有数据；但本方法**不做**
    字段迁移，若修改了表结构需要自行处理旧库（或删除 data/music_studio.db）。
    """
    with connection() as conn:
        conn.executescript(SCHEMA_SQL)
