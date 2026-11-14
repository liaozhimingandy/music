"""service 层：运行时单例装配（依赖注入的组装点）。

路由只依赖这里的 getter，测试可直接替换单例或调用 reset_runtime() 重建。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_song_service: Any = None
_workflow_service: Any = None
_llm_service: Any = None


def get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=max(1, settings.max_workers), thread_name_prefix="musicflow")
    return _executor


def get_workflow_service() -> Any:
    global _workflow_service
    if _workflow_service is None:
        from .workflow_service import WorkflowService

        _workflow_service = WorkflowService()
    return _workflow_service


def _run_job(song_id: str) -> None:
    try:
        get_workflow_service().run(song_id)
    except Exception:  # noqa: BLE001 - 后台线程异常只记录，DB 里已写 failed
        logger.exception("后台生成任务异常结束：%s", song_id)


def submit_job(song_id: str) -> None:
    """POST /api/generations 立即返回，由线程池异步跑五阶段。"""
    get_executor().submit(_run_job, song_id)


def get_song_service() -> Any:
    global _song_service
    if _song_service is None:
        from .song_service import SongService

        _song_service = SongService(submit_job=submit_job)
    return _song_service


def get_llm_service() -> Any:
    global _llm_service
    if _llm_service is None:
        from .llm_service import LLMService

        _llm_service = LLMService()
    return _llm_service


def shutdown() -> None:
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
