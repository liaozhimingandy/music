"""每日生成配额：日界计算、剩余量统计与拒绝文案（纯计算 + 一次 COUNT）。

为什么需要它：每次生成都会真实消耗 LLM 与音乐生成额度，公开部署时必须有
一个「总量上限」兜底，否则被脚本刷一天就会烧掉大量额度。这里把「一天」
定义清楚，避免出现「服务器是 UTC、用户在东八区，凌晨 8 点才重置」的错位。

设计要点
--------
* **日界按时区偏移计算**，不依赖系统 tzdata：容器里通常只有 UTC，Windows
  又常常没装 IANA 时区库，所以直接用 ``timezone(timedelta(hours=offset))``，
  默认 +8（北京时间），需要别的时区改 ``MUSIC_STUDIO_DAILY_RESET_UTC_OFFSET``。
* **计数口径是「创建的 songs 行数」**：排队、生成中、成功、失败都算。
  失败的任务同样可能已经调过 LLM，按创建数统计才不会出现「反复失败无限重试」。
  幂等去重命中已有作品时不会新建行，所以重试不会白吃配额。
* 本模块只做计算与查询，不抛 HTTP 异常；拒绝由 SongService 抛业务异常、
  路由层统一转成 429 + ``Retry-After``（见 routers/generations.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def quota_window(offset_hours: float = 8.0, now: datetime | None = None) -> tuple[datetime, datetime]:
    """返回当前自然日的 ``[start, end)``（按给定 UTC 偏移划分）。

    ``start`` / ``end`` 都是带固定偏移的 aware datetime，可直接 ``isoformat()``
    与 songs.created_at（UTC ISO 字符串）比较——因为都是同一时刻的不同表示，
    字符串比较时区偏移不同会出错，**所以统一先转成 UTC 再格式化**。

    Args:
        offset_hours: 日界所在时区相对 UTC 的小时偏移，东八区传 8。
        now: 便于测试注入的「当前时间」，缺省取系统 UTC 时间。
    """
    tz = timezone(timedelta(hours=offset_hours))
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc), (local_start + timedelta(days=1)).astimezone(timezone.utc)


@dataclass(frozen=True)
class DailyQuota:
    """某一时刻的配额快照，供接口响应头与 /v1/health 展示。"""

    limit: int
    used: int
    reset_at: datetime
    offset_hours: float = 8.0

    @property
    def unlimited(self) -> bool:
        """``limit <= 0`` 视为关闭配额（自建、单人使用场景）。"""
        return self.limit <= 0

    @property
    def remaining(self) -> int | None:
        """剩余可生成数量；不限额时返回 None（前端据此隐藏额度提示）。"""
        return None if self.unlimited else max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        """今日额度是否已用完（不限额永远为 False）。"""
        return not self.unlimited and self.used >= self.limit

    @property
    def retry_after(self) -> int:
        """距明日重置的秒数，用于 429 的 ``Retry-After``；至少 60 秒。"""
        delta = (self.reset_at - datetime.now(timezone.utc)).total_seconds()
        return max(60, int(delta))

    @property
    def reset_hint(self) -> str:
        """重置时间的本地时刻，例如 ``明天 00:00``。"""
        local = self.reset_at.astimezone(timezone(timedelta(hours=self.offset_hours)))
        return local.strftime("%m-%d %H:%M")

    def message(self) -> str:
        """面向用户的中文提示：说清上限、何时恢复、还能做什么。"""
        return (
            f"今日生成额度已用完（每天最多 {self.limit} 首），{self.reset_hint} 自动重置。"
            "可以继续试听作品库里的作品，或明天再来；部署者可用 MUSIC_STUDIO_DAILY_LIMIT 调整上限。"
        )

    def headers(self) -> dict[str, str]:
        """给创建接口用的配额响应头（不限额时不带任何头，避免前端误判）。"""
        if self.unlimited:
            return {}
        return {
            "X-Daily-Limit": str(self.limit),
            "X-Daily-Remaining": str(self.remaining),
            "X-Daily-Reset": self.reset_at.isoformat(),
        }
