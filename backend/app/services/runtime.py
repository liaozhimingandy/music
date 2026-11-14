"""service 层：运行时单例装配（依赖注入的组装点）。

路由只依赖这里的 getter，不直接 new Service；测试可通过替换单例或调用
``reset_runtime()`` 重建，从而让新的环境变量 / mock 设置生效。

为什么用线程池而不是 asyncio：五阶段流程是 httpx 同步阻塞调用，
放线程池最省事，且与 SQLite「一操作一连接」的 DAL 天然契合。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

# 进程级单例；由 main.py 的 lifespan 在关闭时调用 shutdown() 清理。
_executor: ThreadPoolExecutor | None = None
_song_service: Any = None
_workflow_service: Any = None
_llm_service: Any = None


def get_executor() -> ThreadPoolExecutor:
    """惰性创建生成任务线程池，并发上限由 settings.max_workers 控制。"""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=max(1, settings.max_workers), thread_name_prefix="musicflow")
    return _executor


def get_workflow_service() -> Any:
    """惰性创建五阶段编排器（后台线程实际执行的对象）。"""
    global _workflow_service
    if _workflow_service is None:
        # 延迟 import：避免模块加载期形成循环依赖，也便于测试先改配置。
        from .workflow_service import WorkflowService

        _workflow_service = WorkflowService()
    return _workflow_service


def _run_job(song_id: str) -> None:
    """线程池入口：包一层兜底异常，防止未捕获异常让 worker 静默退出。

    阶段失败时 WorkflowService 已把作品置为 failed 并写入 error_message，
    这里只负责记日志，不再改变数据库状态。
    """
    try:
        get_workflow_service().run(song_id)
    except Exception:  # noqa: BLE001 - 后台线程异常只记录，DB 里已写 failed
        logger.exception("后台生成任务异常结束：%s", song_id)


def submit_job(song_id: str) -> None:
    """POST /api/generations 立即返回，由线程池异步跑五阶段。"""
    get_executor().submit(_run_job, song_id)


def get_song_service() -> Any:
    """惰性创建作品服务，并把 submit_job 注入进去（便于单测替换成同步执行）。"""
    global _song_service
    if _song_service is None:
        from .song_service import SongService

        _song_service = SongService(submit_job=submit_job)
    return _song_service


def get_llm_service() -> Any:
    """惰性创建轻量 LLM 服务，供 /api/llm 三个按钮使用。"""
    global _llm_service
    if _llm_service is None:
        from .llm_service import LLMService

        _llm_service = LLMService()
    return _llm_service


def shutdown() -> None:
    """应用关闭时释放线程池并清空单例。

    ``cancel_futures=True`` 表示排队中尚未开始的任务直接丢弃；已经在跑的
    任务不强制中断（进程退出会自然结束），避免写了一半的库记录。
    """
    global _executor, _song_service, _workflow_service, _llm_service
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
    _executor = None
    _song_service = None
    _workflow_service = None
    _llm_service = None


def reset_runtime() -> None:
    """测试用：清掉单例，让新的环境变量 / mock 设置生效。"""
    shutdown()
