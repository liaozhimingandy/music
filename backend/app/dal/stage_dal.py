"""dal 层：song_stages 表读写（全异步），记录五阶段进度与断点续跑状态。

阶段状态：pending -> running -> completed / failed。WorkflowService 每完成
一步就回写一次，前端轮询时能看到实时进度；进程重启后已 completed 的阶段
会依据产物文件直接跳过。
"""

from __future__ import annotations

import json
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

    async def complete(
        self,
        song_id: str,
        stage: str,
        *,
        model: str | None = None,
        usage: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """标记成功：写 completed + finished_at，并落盘阶段遥测。

        Args:
            model: 该阶段实际使用的模型（LLM 模型名 / 音乐生成模型 ID）。
            usage: ``{"prompt_tokens", "completion_tokens", "total_tokens"}``；
                音乐阶段或 mock 客户端没有用量时传 None，列保持 NULL。
            metrics: 扩展指标（elapsed_ms、远端 task_id 等），序列化为
                metrics_json；调用方传 None 时不清空已有值（仅当显式传入
                字典才覆盖）。
        """
        fields: dict[str, Any] = {
            "status": "completed",
            "finished_at": utc_now(),
            "error": None,
        }
        # ``model`` 只在拿到真实值时写入，传 None 时保持数据库里的原值。
        # 早期实现是无条件写 ``"model": model``，于是「跳过复用」的阶段会把上
        # 次记录的模型抹成 NULL，看起来就像「换了模型但数据库没记录」。
        if model:
            fields["model"] = model
        if usage:
            fields.update(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
        if metrics is not None:
            fields["metrics_json"] = json.dumps(metrics, ensure_ascii=False)
        await self.update(song_id, stage, **fields)

    async def reuse(self, song_id: str, stage: str) -> str | None:
        """复用已落盘的阶段产物：只改状态与时间，**不碰遥测列**。

        断点续跑（进程重启后接着跑）会走到这里。之所以单独一个方法而不是
        复用 ``complete()``：``complete()`` 写的是「这一次运行」的遥测，而复用
        时我们根本不知道这次的模型——硬写会把上次的真实值覆盖掉。这里保持
        model / token / metrics 原样，只在 metrics_json 里打一个 ``reused``
        标记，事后就能区分「这个阶段真跑了」还是「吃了上次的产物」。

        Args:
            song_id: 作品 ID。
            stage: 阶段名。

        Returns:
            上次记录的模型名（没有则为 None），供调用方打日志。
        """
        async with connection() as conn:
            cursor = await conn.execute(
                "SELECT model, metrics_json FROM song_stages WHERE song_id = ? AND stage = ?",
                (song_id, stage),
            )
            row = await cursor.fetchone()
            await cursor.close()

            previous_model = row[0] if row else None
            metrics: dict[str, Any] = {}
            if row and row[1]:
                try:
                    loaded = json.loads(row[1])
                except (TypeError, ValueError):  # 历史脏数据不该阻止续跑
                    loaded = None
                if isinstance(loaded, dict):
                    metrics = loaded
            metrics["reused"] = True

            await conn.execute(
                "UPDATE song_stages SET status = ?, finished_at = ?, error = NULL, metrics_json = ? "
                "WHERE song_id = ? AND stage = ?",
                ("completed", utc_now(), json.dumps(metrics, ensure_ascii=False), song_id, stage),
            )
        return previous_model

    async def fail(
        self,
        song_id: str,
        stage: str,
        error: str,
        *,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """标记失败：保存错误文本供前端展示，作品整体会被置为 failed。

        失败同样记录 metrics（如耗时），定位「卡在哪一步、跑了多久」很关键。
        """
        fields: dict[str, Any] = {"status": "failed", "finished_at": utc_now(), "error": error}
        if metrics is not None:
            fields["metrics_json"] = json.dumps(metrics, ensure_ascii=False)
        await self.update(song_id, stage, **fields)

    async def list(self, song_id: str) -> list[StageRecord]:
        """按 id（即 STAGE_ORDER 的插入顺序）返回该作品的全部阶段。"""
        async with connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM song_stages WHERE song_id = ? ORDER BY id", (song_id,)
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [StageRecord.from_row(row) for row in rows]
