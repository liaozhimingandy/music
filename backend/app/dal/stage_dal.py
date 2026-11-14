"""dal 层：song_stages 表读写（全异步），记录五阶段进度与断点续跑状态。

阶段状态：pending -> running -> completed / failed。WorkflowService 每完成
一步就回写一次，前端轮询时能看到实时进度；进程重启后已 completed 的阶段
会依据产物文件直接跳过。
"""

from __future__ import annotations

from typing import Any

from ..constants import STAGE_OUTPUTS, STAGE_ORDER, STAGE_PENDING
from ..models import STAGE_COLUMNS, StageRecord
from .database import connection, utc_now


class StageDAL:
    """阶段进度表的数据访问对象；所有方法均为 async。"""

    async def initialize(self, song_id: str) -> None:
        """为一个新任务预置全部阶段（已存在则不动，支持续跑）。

        ``INSERT OR IGNORE`` 依赖 UNIQUE(song_id, stage)，因此重复调用不会
        把已完成阶段重置回 pending。五条插入共用一个事务。
        """
        async with connection() as conn:
            for stage in STAGE_ORDER:
                await conn.execute(
                    "INSERT OR IGNORE INTO song_stages "
                    "(song_id, stage, status, output_file) VALUES (?, ?, ?, ?)",
                    (song_id, stage, STAGE_PENDING, STAGE_OUTPUTS[stage]),
                )

    async def update(self, song_id: str, stage: str, **fields: Any) -> None:
        """更新某阶段的可写字段；未知字段忽略，空更新直接返回。"""
        normalized = {key: value for key, value in fields.items() if key in STAGE_COLUMNS or key == "status"}
        if not normalized:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in normalized)
        async with connection() as conn:
            await conn.execute(
                f"UPDATE song_stages SET {assignments} WHERE song_id = :song_id AND stage = :stage",
                {**normalized, "song_id": song_id, "stage": stage},
            )

    async def start(self, song_id: str, stage: str) -> None:
        """标记开始：写 running + started_at，并清空上次失败的错误信息。"""
        await self.update(song_id, stage, status="running", started_at=utc_now(), error=None, finished_at=None)

    async def complete(self, song_id: str, stage: str) -> None:
        """标记成功：写 completed + finished_at。"""
        await self.update(song_id, stage, status="completed", finished_at=utc_now(), error=None)

    async def fail(self, song_id: str, stage: str, error: str) -> None:
        """标记失败：保存错误文本供前端展示，作品整体会被置为 failed。"""
        await self.update(song_id, stage, status="failed", finished_at=utc_now(), error=error)

    async def list(self, song_id: str) -> list[StageRecord]:
        """按 id（即 STAGE_ORDER 的插入顺序）返回该作品的全部阶段。"""
        async with connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM song_stages WHERE song_id = ? ORDER BY id", (song_id,)
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [StageRecord.from_row(row) for row in rows]
