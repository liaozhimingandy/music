"""dal 层：songs 表 CRUD，只做数据存取，不含业务判断。

所有 SQL 参数都走占位符绑定，字段来自 models.SONG_COLUMNS 白名单，
因此 Service 传入的任意 kwargs 都不会拼出非预期列或注入 SQL。
"""

from __future__ import annotations

import json
from typing import Any

from ..models import SONG_COLUMNS, SongRecord
from .database import connection, utc_now


class SongDAL:
    """作品表的数据访问对象；方法均返回实体，不返回 sqlite3.Row。"""

    def create(self, record: SongRecord) -> SongRecord:
        """插入一条新作品，并补齐创建 / 更新时间。

        ``to_row()`` 已把 bool、metadata 转成数据库可接受的类型；这里再按
        SONG_COLUMNS 过滤一次，保证列名与 DDL 严格一致。
        """
        now = utc_now()
        record.created_at = record.created_at or now
        record.updated_at = record.updated_at or now
        row = {key: value for key, value in record.to_row().items() if key in SONG_COLUMNS}
        columns = ", ".join(row)
        placeholders = ", ".join(f":{key}" for key in row)
        with connection() as conn:
            conn.execute(
                f"INSERT INTO songs ({columns}) VALUES ({placeholders})",
                row,
            )
        return record

    def get(self, song_id: str) -> SongRecord | None:
        """按 ID 查单条；不存在返回 None（由路由层转成 404）。"""
        with connection() as conn:
            row = conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        return SongRecord.from_row(row) if row else None

    def list(self, limit: int = 25) -> list[SongRecord]:
        """按创建时间倒序列出作品，供 /api/history 渲染作品库。

        limit 钳制在 1~200，避免前端误传超大值拖慢接口；排序依赖
        idx_songs_created_at 索引。
        """
        with connection() as conn:
            rows = conn.execute(
                "SELECT * FROM songs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        return [SongRecord.from_row(row) for row in rows]

    def update(self, song_id: str, **fields: Any) -> SongRecord | None:
        """局部更新字段，返回更新后的实体。

        归一化规则（与 entities 的转换互逆）：
        * ``metadata`` dict -> ``metadata_json`` 字符串；
        * ``instrumental`` bool -> 0/1；
        * 其余键必须在 SONG_COLUMNS 内，未知键静默忽略。

        每次更新都会自动刷新 ``updated_at``，无需调用方传入。
        """
        normalized: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "metadata":
                normalized["metadata_json"] = json.dumps(value, ensure_ascii=False)
            elif key == "instrumental":
                normalized[key] = int(bool(value))
            elif key in SONG_COLUMNS:
                normalized[key] = value
        if not normalized:
            return self.get(song_id)
        normalized["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = :{key}" for key in normalized)
        with connection() as conn:
            conn.execute(
                f"UPDATE songs SET {assignments} WHERE id = :song_id",
                {**normalized, "song_id": song_id},
            )
        return self.get(song_id)

    def delete(self, song_id: str) -> bool:
        """删除作品；因外键 ON DELETE CASCADE，阶段记录会一并删除。

        返回是否真的删到了行，供路由层区分 204 与 404。音频文件由
        SongService 单独清理，不在这里做，避免 DAL 依赖文件系统。
        """
        with connection() as conn:
            cursor = conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
            return cursor.rowcount > 0
