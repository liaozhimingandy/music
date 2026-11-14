"""FastAPI 依赖层：服务注入 + 接口防护（防重复提交 / 防恶意调接口）。

路由一律用 ``Depends(get_song_service)`` / ``Depends(get_llm_service)`` 取服务，
不在函数体里直接调 getter；本模块提供四类可复用的防护依赖：

* ``reject_oversized_body``  Content-Length 超上限 -> 413（router 级统一挂）
* ``enforce_read_policy``    读接口滑动窗口限流 -> 429
* ``enforce_llm_policy``     /v1/llm 限流（会消耗 LLM 额度，阈值更严）
* ``enforce_generation_policy`` 生成接口限流（并发闸门在 SongService 去重之后）

``client_key`` 把客户端 IP 做哈希后既用于限流分桶，也写进 songs 表的
``client_key`` 列做单客户端活跃数统计；默认**不信任** X-Forwarded-For，
因为该头可被任意伪造，只有部署在可信反代后才开 MUSIC_STUDIO_TRUST_PROXY。

与分层的关系：本层属于接口层（与 routers 同级），只做校验与策略判断；
真正的去重落库在 SongService.create，计数查询在 SongDAL。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated

from fastapi import HTTPException, Path, Request

from .config import settings
from .services.rate_limiter import RateLimitExceeded, SlidingWindowLimiter

logger = logging.getLogger(__name__)

# 路径参数白名单：32 位小写 hex，兼容历史 uuid4 与新的 uuid7 作品 ID。
# 配合 SongService.delete 里的 outputs 根目录校验，形成两道目录穿越防线。
SongId = Annotated[
    str,
    Path(pattern=r"^[0-9a-f]{32}$", description="作品 ID（32 位小写十六进制）"),
]

# 三个独立的滑动窗口限流器（按接口危险程度分别设阈值）。
_limiters: dict[str, SlidingWindowLimiter] = {}


def _build_limiters() -> dict[str, SlidingWindowLimiter]:
    """按当前 settings 构造限流器；改配置后调 ``reset_limiters()`` 生效。"""
    enabled = settings.rate_limit_enabled
    window = settings.rate_limit_window_seconds
    return {
        "read": SlidingWindowLimiter(
            max_requests=settings.rate_limit_read, window_seconds=window, enabled=enabled, scope="读取接口"
        ),
        "llm": SlidingWindowLimiter(
            max_requests=settings.rate_limit_llm, window_seconds=window, enabled=enabled, scope="AI 文案接口"
        ),
        "create": SlidingWindowLimiter(
            max_requests=settings.rate_limit_create, window_seconds=window, enabled=enabled, scope="生成接口"
        ),
    }


def get_limiter(scope: str) -> SlidingWindowLimiter:
    """惰性取限流器单例（同一进程内所有请求共享计数）。"""
    if scope not in _limiters:
        _limiters.update(_build_limiters())
    return _limiters[scope]


def reset_limiters() -> None:
    """测试用：按最新配置重建限流器并清空已有计数。"""
    _limiters.clear()
    _limiters.update(_build_limiters())


def client_key(request: Request) -> str:
    """把客户端 IP 转成 16 位十六进制标识。

    存哈希而非明文 IP：既能做分桶统计，又不在数据库里留下可直接识别的
    网络地址。哈希长度 16 hex（64 bit）对单机分桶足够，碰撞概率可忽略。
    """
    raw = ""
    if settings.trust_proxy_headers:
        raw = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not raw and request.client:
        raw = request.client.host
    return hashlib.sha256((raw or "unknown").encode("utf-8")).hexdigest()[:16]


def _too_many_requests(exc: RateLimitExceeded) -> HTTPException:
    """把限流异常转成带 Retry-After 的 429，方便客户端退避重试。"""
    return HTTPException(
        status_code=429,
        detail=str(exc),
        headers={"Retry-After": str(int(exc.retry_after))},
    )


async def reject_oversized_body(request: Request) -> None:
    """请求体大小上限（依据 Content-Length），超限返回 413。

    chunked 传输没有 Content-Length 时跳过这一层，由 Pydantic 的字段长度
    与 metadata 体积校验兜底，不会出现「无法解析却已读进内存」的情况。
    """
    raw = request.headers.get("content-length")
    if raw and raw.isdigit() and int(raw) > settings.max_request_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"请求体过大（上限 {settings.max_request_bytes} 字节）",
        )


async def enforce_read_policy(request: Request) -> None:
    """读接口限流：覆盖作品库轮询、详情、阶段、音频与删除。"""
    try:
        await get_limiter("read").check(client_key(request))
    except RateLimitExceeded as exc:
        logger.warning("读接口限流命中：%s", client_key(request))
        raise _too_many_requests(exc) from exc


async def enforce_llm_policy(request: Request) -> None:
    """/v1/llm 限流：这三个按钮会真实消耗 LLM 额度，阈值比读接口严。"""
    try:
        await get_limiter("llm").check(client_key(request))
    except RateLimitExceeded as exc:
        logger.warning("LLM 接口限流命中：%s", client_key(request))
        raise _too_many_requests(exc) from exc


async def enforce_generation_policy(request: Request) -> None:
    """生成接口的**请求频率**闸门，返回 429 + Retry-After。

    「全局 / 单客户端活跃任务上限」刻意不放在这里：它是创建业务的一部分，
    必须等 SongService 先做完幂等判断——否则一次合法的 Idempotency-Key
    重试（本来只是取回已有作品、不会新增任务）也会被并发闸门误拦。
    那道闸门在 ``SongService.create`` 内的去重之后执行。

    同理，命中限流时不会创建任何作品记录；内容重复则由 SongService
    幂等处理成 200 + 已有记录（``X-Deduplicated: true``），两者语义不同。
    """
    if not settings.rate_limit_enabled:
        return
    key = client_key(request)
    try:
        await get_limiter("create").check(key)
    except RateLimitExceeded as exc:
        logger.warning("生成接口限流命中：%s", key)
        raise _too_many_requests(exc) from exc
