"""dal 层：songs 表 CRUD（全异步），只做数据存取，不含业务判断。

所有 SQL 参数都走占位符绑定，字段来自 models.SONG_COLUMNS 白名单，
因此 Service 传入的任意 kwargs 都不会拼出非预期列或注入 SQL。

除基本 CRUD 外还提供给接口防护用的三个查询：
``find_duplicate``（幂等去重）/ ``count_active``（全局并发上限）/
``count_active_by_client``（单客户端并发上限）。

持久化队列
----------
``songs`` 表本身就是队列：``queued`` / ``processing`` 的行 = 待执行任务，
``asyncio.Task`` 只是执行载体。``list_unfinished`` 供 worker 扫描，
``claim_recovery`` 用一条带条件的 UPDATE 原子认领（同时递增恢复次数），
``clear_recovery_attempts`` 在任务正常完成后把计数清零。
这样进程重启、--reload、任务被 cancel 都不会丢任务。
"""

from __future__ import annotations

import json
from typing import Any

from ..constants import STATUS_FAILED, STATUS_PROCESSING, STATUS_QUEUED
from ..models import SONG_COLUMNS, SongRecord
from .database import connection, utc_now


class SongDAL:
    """作品表的数据访问对象；所有方法均为 async，返回实体而非 sqlite3.Row。"""

    async def create(self, record: SongRecord) -> SongRecord:
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
        async with connection() as conn:
            await conn.execute(
                f"INSERT INTO songs ({columns}) VALUES ({placeholders})",
                row,
            )
        return record

    async def get(self, song_id: str) -> SongRecord | None:
        """按 ID 查单条；不存在返回 None（由路由层转成 404）。"""
        async with connection() as conn:
            cursor = await conn.execute("SELECT * FROM songs WHERE id = ?", (song_id,))
            row = await cursor.fetchone()
            await cursor.close()
        return SongRecord.from_row(row) if row else None

    async def list(self, limit: int = 25) -> list[SongRecord]:
        """按创建时间倒序列出作品，供 /v1/history 渲染作品库。

        limit 钳制在 1~200，避免前端误传超大值拖慢接口；排序依赖
        idx_songs_created_at 索引。路由层还会用 Query(le=100) 再校一次。
        """
        async with connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM songs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [SongRecord.from_row(row) for row in rows]

    async def find_duplicate(self, fingerprint: str, since: str) -> SongRecord | None:
        """查同一请求指纹下可复用的作品，用于幂等去重。

        两类命中：
        * 仍在 ``queued`` / ``processing`` 的任务 —— 无论过了多久都复用，
          防止重复提交把同一个生成任务跑两遍、重复计费；
        * 窗口内刚创建的任务（含 ready / failed）—— 防双击与网络重试，
          窗口过后允许重新生成相同内容。

        ``since`` 是 ISO 8601 UTC 字符串，与 created_at 同格式，可直接比较。
        依赖 idx_songs_fingerprint 索引。
        """
        async with connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM songs WHERE input_fingerprint = ? "
                "AND (status IN (?, ?) OR created_at >= ?) "
                "ORDER BY created_at DESC LIMIT 1",
                (fingerprint, STATUS_QUEUED, STATUS_PROCESSING, since),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return SongRecord.from_row(row) if row else None

    async def count_active(self) -> int:
        """统计全局排队中 / 生成中的任务数（防刷爆线程池与外部 API 额度）。"""
        async with connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM songs WHERE status IN (?, ?)",
                (STATUS_QUEUED, STATUS_PROCESSING),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return int(row[0]) if row else 0

    async def count_active_by_client(self, client_key: str) -> int:
        """统计单个客户端的活跃任务数，防止一个 IP 占满全部生成槽位。"""
        async with connection() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM songs WHERE client_key = ? AND status IN (?, ?)",
                (client_key, STATUS_QUEUED, STATUS_PROCESSING),
            )
            row = await cursor.fetchone()
            await cursor.close()
        return int(row[0]) if row else 0

    # ---------------- 持久化队列 ----------------

    async def list_unfinished(self, limit: int = 200) -> list[SongRecord]:
        """列出所有 ``queued`` / ``processing`` 的任务，按创建时间升序（FIFO）。

        这是重启恢复的依据：DB 里的未完成行不会因为进程退出而消失。
        ``ready`` / ``failed`` 是终态，不会进入队列；``recovery_attempts``
        是否超限由 runtime 判断，这里只负责取数，保持 DAL 不含业务策略。
        依赖 idx_songs_queue 索引。
        """
        async with connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM songs WHERE status IN (?, ?) ORDER BY created_at ASC LIMIT ?",
                (STATUS_QUEUED, STATUS_PROCESSING, max(1, min(limit, 500))),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [SongRecord.from_row(row) for row in rows]

    async def claim_recovery(self, song_id: str, max_attempts: int) -> bool:
        """原子认领一个待恢复任务：重置成 queued、清旧错误、恢复次数 +1。

        WHERE 里同时检查状态与次数上限，保证并发（或多进程）扫描时不会把
        同一个任务认领两次；返回是否真的更新到行。重置 ``current_stage``
        是为了让前端在重新排队期间不显示上一个进程留下的阶段进度。
        """
        async with connection() as conn:
            cursor = await conn.execute(
                "UPDATE songs SET status = ?, current_stage = NULL, error_message = NULL, "
                "recovery_attempts = recovery_attempts + 1, updated_at = ? "
                "WHERE id = ? AND status IN (?, ?) AND recovery_attempts < ?",
                (STATUS_QUEUED, utc_now(), song_id, STATUS_QUEUED, STATUS_PROCESSING, max_attempts),
            )
            claimed = cursor.rowcount > 0
            await cursor.close()
        return claimed

    async def mark_recovery_exhausted(self, song_id: str) -> None:
        """恢复次数用尽：置为 failed 并写明原因，避免任务无限重试。"""
        async with connection() as conn:
            await conn.execute(
                "UPDATE songs SET status = ?, current_stage = NULL, "
                "error_message = ?, updated_at = ? "
                "WHERE id = ? AND status IN (?, ?)",
                (
                    STATUS_FAILED,
                    "任务多次中断且未完成，已停止自动恢复；请重新提交生成",
                    utc_now(),
                    song_id,
                    STATUS_QUEUED,
                    STATUS_PROCESSING,
                ),
            )

    async def clear_recovery_attempts(self, song_id: str) -> None:
        """任务正常跑完后清零恢复次数（只统计「连续中断」次数）。"""
        async with connection() as conn:
            await conn.execute(
                "UPDATE songs SET recovery_attempts = 0 WHERE id = ?", (song_id,)
            )

    async def update(self, song_id: str, **fields: Any) -> SongRecord | None:
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
            return await self.get(song_id)
        normalized["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = :{key}" for key in normalized)
        async with connection() as conn:
            await conn.execute(
                f"UPDATE songs SET {assignments} WHERE id = :song_id",
                {**normalized, "song_id": song_id},
            )
        # 在连接关闭后再读，避免同一事务内另开连接造成不必要的锁竞争。
        return await self.get(song_id)

    async def delete(self, song_id: str) -> bool:
        """删除作品；因外键 ON DELETE CASCADE，阶段记录会一并删除。

        返回是否真的删到了行，供路由层区分 200 与 404。音频文件由
        SongService 单独清理，不在这里做，避免 DAL 依赖文件系统。
        """
        async with connection() as conn:
            cursor = await conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
            deleted = cursor.rowcount > 0
            await cursor.close()
        return deleted
