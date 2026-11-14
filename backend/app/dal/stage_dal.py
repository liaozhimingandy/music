"""dal 层：song_stages 表读写，记录五阶段进度与断点续跑状态。"""

from __future__ import annotations

from typing import Any

from ..constants import STAGE_OUTPUTS, STAGE_ORDER, STAGE_PENDING
from ..models import STAGE_COLUMNS, StageRecord
from .database import connection, utc_now


class StageDAL:
    def initialize(self, song_id: str) -> None:
        """为一个新任务预置全部阶段（已存在则不动，支持续跑）。"""
        with connection() as conn:
            for stage in STAGE_ORDER:
                conn.execute(
                    "INSERT OR IGNORE INTO song_stages "
                    "(song_id, stage, status, output_file) VALUES (?, ?, ?, ?)",
                    (song_id, stage, STAGE_PENDING, STAGE_OUTPUTS[stage]),
                )

    def update(self, song_id: str, stage: str, **fields: Any) -> None:
        normalized = {key: value for key, value in fields.items() if key in STAGE_COLUMNS or key == "status"}
        if not normalized:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in normalized)
        with connection() as conn:
            conn.execute(
                f"UPDATE song_stages SET {assignments} WHERE song_id = :song_id AND stage = :stage",
                {**normalized, "song_id": song_id, "stage": stage},
            )

    def start(self, song_id: str, stage: str) -> None:
        self.update(song_id, stage, status="running", started_at=utc_now(), error=None, finished_at=None)

    def complete(self, song_id: str, stage: str) -> None:
        self.update(song_id, stage, status="completed", finished_at=utc_now(), error=None)

    def fail(self, song_id: str, stage: str, error: str) -> None:
        self.update(song_id, stage, status="failed", finished_at=utc_now(), error=error)

    def list(self, song_id: str) -> list[StageRecord]:
        with connection() as conn:
            rows = conn.execute(
                "SELECT * FROM song_stages WHERE song_id = ? ORDER BY id", (song_id,)
            ).fetchall()
        return [StageRecord.from_row(row) for row in rows]
