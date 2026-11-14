"""dal 层：songs 表 CRUD，只做数据存取，不含业务判断。"""

from __future__ import annotations

import json
from typing import Any

from ..models import SONG_COLUMNS, SongRecord
from .database import connection, utc_now


class SongDAL:
    def create(self, record: SongRecord) -> SongRecord:
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
        with connection() as conn:
            row = conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        return SongRecord.from_row(row) if row else None

    def list(self, limit: int = 25) -> list[SongRecord]:
        with connection() as conn:
            rows = conn.execute(
                "SELECT * FROM songs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        return [SongRecord.from_row(row) for row in rows]

    def update(self, song_id: str, **fields: Any) -> SongRecord | None:
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
        with connection() as conn:
            cursor = conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
            return cursor.rowcount > 0
