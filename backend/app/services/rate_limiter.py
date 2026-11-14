"""service 层：进程内滑动窗口限流器（防接口滥用）。

设计取舍
--------
* **为什么是进程内**：Music Studio 是单机本地工作台，默认单 uvicorn
  worker，内存限流足够且零依赖。若将来多 worker / 多机部署，需要把
  ``check`` 换成 Redis 的原子计数（接口不变），注释里已标出替换点。
* **为什么按客户端而不是全局**：全局限流会让一个脚本拖垮所有人；
  按 IP 哈希分桶，再叠加 deps.py 里的「全局活跃任务上限」兜底。
* 计数用 ``time.monotonic()``，不受系统时钟调整影响；过期记录惰性清理，
  并在桶数量过大时全量清扫，避免恶意构造海量 IP 撑爆内存。

所有方法均为 async，``asyncio.Lock`` 保护同一进程内的并发增减。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    """窗口内请求数超限；由 deps.py 统一转换成 HTTP 429。"""

    def __init__(self, *, scope: str, retry_after: float) -> None:
        self.scope = scope
        self.retry_after = max(1.0, retry_after)
        super().__init__(f"{scope} 请求过于频繁，请 {int(self.retry_after)} 秒后重试")


class SlidingWindowLimiter:
    """按 key 统计 ``window_seconds`` 内的请求次数，超过上限即抛异常。

    Args:
        max_requests: 窗口内允许的最大请求数。
        window_seconds: 滑动窗口长度（秒）。
        enabled: 关闭后 ``check`` 直接返回，供本地调试 / 冒烟测试使用。
        scope: 出现在错误文案里的标识（如 ``create`` / ``llm`` / ``read``）。
    """

    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: float,
        enabled: bool = True,
        scope: str = "requests",
    ) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(1.0, float(window_seconds))
        self.enabled = enabled
        self.scope = scope
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        """登记一次请求；超限时抛 ``RateLimitExceeded``。

        多 worker 部署时把这里换成 Redis ``INCR + EXPIRE`` 即可，
        上层 deps.py 不需要改。
        """
        if not self.enabled:
            return
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_requests:
                raise RateLimitExceeded(scope=self.scope, retry_after=hits[0] + self.window_seconds - now)
            hits.append(now)
            # 惰性清扫：客户端基数异常大时回收长期不活跃的桶。
            if len(self._hits) > 4096:
                self._prune(cutoff)

    def _prune(self, cutoff: float) -> None:
        """删除所有已完全过期的 key（调用方已持有 _lock）。"""
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            self._hits.pop(key, None)

    def reset(self) -> None:
        """清空计数，供测试在改完阈值后重新开始一个窗口。"""
        self._hits.clear()
