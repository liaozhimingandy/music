"""service 层：运行时单例装配 + 持久化任务队列（FastAPI Depends 的组装点）。

路由只依赖这里的 getter，不直接 new Service；测试可通过 ``reset_runtime()``
重建，让新的环境变量 / mock 设置生效。

队列模型：数据库才是队列，asyncio.Task 只是执行载体
------------------------------------------------
``songs`` 表里 ``status IN ('queued', 'processing')`` 的每一行就是一个待办
任务。POST /v1/generations 先落库再 ``submit_job``；应用启动时
``start_queue_worker()`` 立刻扫描一次未完成记录并重新入队，之后每
``settings.queue_poll_seconds`` 秒巡检一次，兜住任务崩溃 / 新建后提交失败
等边角情况。因此 uvicorn 重启、--reload、Ctrl+C 都不会丢任务。

关闭语义（这是「重启能继续」的关键）：``shutdown()`` 只 cancel 内存里的
task，**不改数据库状态**。被中断的任务仍留在 queued/processing，下次启动
由 worker 认领；WorkflowService 依据已落盘的阶段产物断点续跑，
MusicProvider 依据 music_task.json 续用同一个远端 task_id，不重复计费。

只有两类情况不会继续：
* 接口层的限制（413 / 422 / 429 / 幂等去重）——请求根本没落库；
* 真实异常——WorkflowService 会把作品置为 failed（终态），不入队；
  反复中断超过 ``max_recovery_attempts`` 的任务也会被标为 failed，
  避免无限重跑消耗 LLM / 音乐生成额度。

其它实现要点
------------
* ``_running`` 按 song_id 去重，防止「启动恢复」与「新请求」重复入队；
* ``_tasks`` 持有 task 强引用（asyncio 只弱引用，否则可能被 GC 回收）；
* ``asyncio.Semaphore`` 限制同时执行的作品数，按 event loop 缓存，
  避免 TestClient 每次新建 loop 后复用旧信号量报错；
* 本实现面向**单进程 uvicorn**；若要多 worker，需要额外加行级租约。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..config import settings
from ..constants import STATUS_FAILED, STATUS_READY
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
_running: dict[str, asyncio.Task[None]] = {}  # song_id -> 正在执行的 task
_queue_worker: asyncio.Task[None] | None = None
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
    """单个任务：拿信号量 -> 跑五阶段 -> 处理终态。

    * 正常完成：清零恢复次数（只统计连续中断次数）；
    * ``CancelledError``（应用关闭）：**保留 DB 里的 queued/processing**，
      等下次启动重新入队，绝不写成 failed；
    * 其它异常：WorkflowService 通常已写 failed + error_message，这里仅
      在状态仍是 queued/processing 时补写，避免 worker 无限重试。
    """
    songs = SongDAL()
    try:
        async with get_semaphore():
            workflow = await get_workflow_service()
            await workflow.run(song_id)
        await songs.clear_recovery_attempts(song_id)
    except asyncio.CancelledError:
        # 故意不改状态：进程重启后由 worker 认领，配合阶段产物断点续跑。
        logger.info("生成任务随应用关闭暂停，保留队列状态等待重启续跑：%s", song_id)
        raise
    except Exception as exc:  # noqa: BLE001 - 后台任务异常不能影响 event loop
        logger.exception("后台生成任务异常结束：%s", song_id)
        try:
            record = await songs.get(song_id)
            if record is not None and record.status not in (STATUS_FAILED, STATUS_READY):
                await songs.update(
                    song_id,
                    status=STATUS_FAILED,
                    current_stage=None,
                    error_message=f"生成任务异常：{exc}",
                )
        except Exception:  # noqa: BLE001 - 兜底写库失败也不再向外抛
            logger.exception("标记失败任务时出错：%s", song_id)


async def submit_job(song_id: str) -> None:
    """把 song_id 交给后台 asyncio 任务；同一 ID 重复调用只会入队一次。

    ``_tasks`` 必须持有强引用（asyncio 只弱引用 task），``_running`` 则用于
    去重：队列巡检与新请求可能同时看到同一个未完成任务。done 回调里比较
    task 对象再删除，避免极端情况下误删后来者。
    """
    if song_id in _running:
        return
    task = asyncio.create_task(_run_job(song_id), name=f"musicflow-{song_id}")
    _tasks.add(task)
    _running[song_id] = task

    def _on_done(finished: asyncio.Task[None]) -> None:
        _tasks.discard(finished)
        if _running.get(song_id) is finished:
            _running.pop(song_id, None)

    task.add_done_callback(_on_done)


async def recover_pending_jobs() -> int:
    """扫描 DB 中的未完成任务并重新入队；返回本次实际入队数量。

    每次认领都会 ``recovery_attempts + 1``（原子 UPDATE 带条件），次数超限
    则直接标记为 failed，防止一个反复崩溃的任务无限重跑。
    新建任务（attempts=0）在第一次重启恢复后也会 +1，但跑完会清零。
    """
    max_attempts = settings.max_recovery_attempts
    if max_attempts <= 0:
        return 0
    songs = SongDAL()
    records = await songs.list_unfinished(limit=500)
    recovered = 0
    for record in records:
        if record.id in _running:
            continue  # 本进程已在跑（含刚由 POST 提交的任务）
        if record.recovery_attempts >= max_attempts:
            await songs.mark_recovery_exhausted(record.id)
            logger.warning("任务恢复次数超限，已停止自动恢复：%s", record.id)
            continue
        if await songs.claim_recovery(record.id, max_attempts):
            await submit_job(record.id)
            recovered += 1
            logger.info(
                "恢复未完成的生成任务：%s（第 %d 次）",
                record.id,
                record.recovery_attempts + 1,
            )
    return recovered


async def _queue_loop() -> None:
    """周期性巡检；单轮异常只记日志，下一轮继续，不让 worker 静默死掉。"""
    interval = max(0.5, settings.queue_poll_seconds)
    while True:
        try:
            count = await recover_pending_jobs()
            if count:
                logger.info("队列巡检：重新入队 %d 个任务", count)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 巡检失败不能让整个队列停摆
            logger.exception("任务队列巡检失败，将在下一轮重试")
        await asyncio.sleep(interval)


async def start_queue_worker() -> None:
    """启动时调用：立即恢复一次遗留任务，再挂上周期性巡检。

    「立即」很关键——真实生成可能耗时几十分钟，不能让任务等到第一个
    巡检间隔才开始；周期巡检只是兜底，不是恢复的主路径。
    """
    global _queue_worker
    if settings.max_recovery_attempts <= 0:
        logger.info("自动恢复已禁用（MUSIC_STUDIO_MAX_RECOVERY_ATTEMPTS=0）")
        return
    if _queue_worker is not None and not _queue_worker.done():
        return
    try:
        count = await recover_pending_jobs()
        if count:
            logger.info("启动恢复：重新入队 %d 个未完成任务", count)
    except Exception:  # noqa: BLE001 - 恢复失败不阻断应用启动，巡检会再试
        logger.exception("启动恢复扫描失败，将由周期巡检重试")
    _queue_worker = asyncio.create_task(_queue_loop(), name="musicflow-queue-worker")


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
    """惰性创建轻量 LLM 服务，供 /v1/llm 三个按钮使用。"""
    global _llm_service
    if _llm_service is None:
        from .llm_service import LLMService

        _llm_service = LLMService()
    return _llm_service


def active_task_count() -> int:
    """当前仍在跑（含等待信号量）的后台任务数，便于日志与健康检查观测。"""
    return len(_running)


async def shutdown() -> None:
    """应用关闭：停巡检 -> cancel 任务 -> 释放连接池与单例。

    **不把任务标成 failed**：DB 里的 queued/processing 就是待恢复队列，
    cancel 只是暂停本进程的执行载体。gather 等待被取消的任务收尾，
    确保没有写了一半的 DB 事务，然后才由 lifespan 关闭数据库连接。
    """
    global _queue_worker, _semaphore, _semaphore_loop
    global _song_service, _workflow_service, _llm_service
    worker, _queue_worker = _queue_worker, None
    if worker is not None and not worker.done():
        worker.cancel()
    pending = list(_tasks)
    for task in pending:
        task.cancel()
    if worker is not None:
        pending.append(worker)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    _tasks.clear()
    _running.clear()
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
