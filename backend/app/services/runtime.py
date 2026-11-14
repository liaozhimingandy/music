"""service 层：运行时单例装配（FastAPI Depends 的组装点）。

路由只依赖这里的 getter，不直接 new Service；测试可通过 ``reset_runtime()``
重建，让新的环境变量 / mock 设置生效。

异步模型
--------
全链路已改为 asyncio：五阶段流程用 ``asyncio.create_task`` 后台执行，
``asyncio.Semaphore`` 限制同时跑的作品数（上限来自 settings.max_active_jobs）。
相比原来的 ThreadPoolExecutor：
* httpx.AsyncClient / aiosqlite 都能在 event loop 上并发，不再需要线程池；
* 任务集合 ``_tasks`` 持有强引用（否则可能被 GC 回收），关闭时可统一 cancel；
* 数据库与文件 IO 都在各自层内异步化，不会阻塞轮询请求。

注意 ``asyncio.Semaphore`` 与运行中的 event loop 绑定，这里按 loop 缓存，
避免 TestClient 每次新建 loop 后复用旧信号量报错。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..config import settings
from ..constants import STATUS_FAILED
from ..dal import SongDAL

if TYPE_CHECKING:
    from .llm_service import LLMService
    from .song_service import SongService
    from .workflow_service import WorkflowService

logger = logging.getLogger(__name__)

# 进程级单例；由 main.py 的 async lifespan 在关闭时调用 shutdown() 清理。
_song_service: SongService | None = None
_workflow_service: WorkflowService | None = None
_llm_service: LLMService | None = None
_tasks: set[asyncio.Task[None]] = set()
_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def get_semaphore() -> asyncio.Semaphore:
    """惰性创建生成任务信号量；event loop 变化时自动重建。"""
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(max(1, settings.max_active_jobs))
        _semaphore_loop = loop
    return _semaphore


async def get_workflow_service() -> WorkflowService:
    """惰性创建五阶段编排器（后台任务实际执行的对象）。"""
    global _workflow_service
    if _workflow_service is None:
        # 延迟 import：避免模块加载期形成循环依赖，也便于测试先改配置。
        from .workflow_service import WorkflowService

        _workflow_service = WorkflowService()
    return _workflow_service


async def _run_job(song_id: str) -> None:
    """后台任务入口：拿信号量 -> 跑五阶段 -> 兜底记日志。

    阶段失败时 WorkflowService 已把作品置为 failed 并写入 error_message，
    这里只负责记日志；应用关闭触发 cancel 时额外把作品标为 failed，
    避免永远停在 processing 让前端一直轮询。
    """
    try:
        async with get_semaphore():
            workflow = await get_workflow_service()
            await workflow.run(song_id)
    except asyncio.CancelledError:
        logger.warning("生成任务被取消（应用关闭）：%s", song_id)
        try:
            await SongDAL().update(
                song_id,
                status=STATUS_FAILED,
                current_stage=None,
                error_message="服务关闭，生成任务已中断",
            )
        except Exception:  # noqa: BLE001 - 关闭阶段不再抛出二次异常
            logger.exception("标记中断任务失败：%s", song_id)
        raise
    except Exception:  # noqa: BLE001 - 后台任务异常只记录，DB 里已写 failed
        logger.exception("后台生成任务异常结束：%s", song_id)


async def submit_job(song_id: str) -> None:
    """POST /api/generations 立即返回，由 asyncio 任务在后台跑五阶段。

    ``_tasks`` 必须持有强引用：asyncio 只弱引用 task，不保存可能被 GC 提前
    回收；``discard`` 回调保证任务结束后集合不会无限增长。
    """
    task = asyncio.create_task(_run_job(song_id), name=f"musicflow-{song_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def get_song_service() -> SongService:
    """惰性创建作品服务，并把异步 ``submit_job`` 注入进去。

    这是 FastAPI ``Depends(get_song_service)`` 直接使用的依赖函数；
    同一请求内多处 Depends 会被 FastAPI 缓存，只实例化一次。
    """
    global _song_service
    if _song_service is None:
        from .song_service import SongService

        _song_service = SongService(submit_job=submit_job)
    return _song_service


async def get_llm_service() -> LLMService:
    """惰性创建轻量 LLM 服务，供 /api/llm 三个按钮使用。"""
    global _llm_service
    if _llm_service is None:
        from .llm_service import LLMService

        _llm_service = LLMService()
    return _llm_service


def active_task_count() -> int:
    """当前仍在跑的后台任务数，便于日志与健康检查观测。"""
    return len(_tasks)


async def shutdown() -> None:
    """应用关闭时取消未完成任务并清空单例（由 async lifespan 调用）。

    cancel 后短暂 gather 等待任务收尾（写入 failed 状态），避免进程退出时
    数据库写到一半被强行断开。
    """
    global _semaphore, _semaphore_loop, _song_service, _workflow_service, _llm_service
    pending = list(_tasks)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _tasks.clear()
    # 释放两个服务各自持有的 httpx.AsyncClient 连接池。
    for service in (_workflow_service, _llm_service):
        if service is not None:
            try:
                await service.aclose()
            except Exception:  # noqa: BLE001 - 关闭阶段的清理失败不影响退出
                logger.exception("关闭 LLM 连接池失败：%s", type(service).__name__)
    _semaphore = None
    _semaphore_loop = None
    _song_service = None
    _workflow_service = None
    _llm_service = None


async def reset_runtime() -> None:
    """测试用：清掉单例与任务，让新的环境变量 / mock 设置生效。"""
    await shutdown()
