"""service 层：面向 API 的作品服务（全异步 + 幂等去重）。

职责：把前端 GenerationCreate 转成数据库记录 -> 启动后台生成任务 ->
把 SongRecord 映射成前端需要的 GenerationResponse。
这是「前端契约」与「数据库实体」之间唯一的转换点，改字段优先改这里。

生成接口的三道防护
------------------
1. **幂等去重**：对规范化后的请求内容算 sha256 指纹（或直接用客户端的
   ``Idempotency-Key``）。若同一指纹已有 queued/processing 任务，或窗口内
   刚创建过作品，就直接返回已有记录（``deduplicated=True``），不再启新任务、
   不重复调用 LLM / 音乐生成接口。前端无感，响应结构与新建完全一致。
2. **并发闸门**：``_create_lock`` 把「查重 + 插入」串行化，杜绝并发双击时
   两个请求同时查不到、各自插一条；deps.py 另有滑动窗口限流与全局/单客户端
   活跃任务上限。
3. **每日配额**：``MUSIC_STUDIO_DAILY_LIMIT``（默认 50 首/天，0 表示不限）
   限制「一天能建多少首」，是成本兜底。它排在幂等判断之后，所以幂等重试与
   查询接口都不吃配额；只有真新建才会消耗。

注意：本类不执行五阶段流程，具体编排在 WorkflowService；
生成任务通过构造参数 ``submit_job``（async 回调）注入，单测可换成同步执行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import settings
from ..constants import AUDIO_URL_TEMPLATE, STAGE_LABELS, STATUS_FAILED, STATUS_QUEUED, USER_INPUT_FILE
from ..dal import ArtifactStore, SongDAL, StageDAL
from ..ids import new_song_id
from ..models import (
    GenerationCreate,
    GenerationDetailResponse,
    GenerationResponse,
    SongRecord,
    StageResponse,
)
from .cover_theme import compute_theme
from .quota import DailyQuota, quota_window

logger = logging.getLogger(__name__)


def _fallback_title(prompt: str) -> str:
    """用户没填标题时，取描述前 18 字作为临时标题（后续可能被润色阶段覆盖）。"""
    cleaned = " ".join(prompt.split())
    return (cleaned[:18] or "未命名作品").strip()


def request_fingerprint(payload: GenerationCreate, idempotency_key: str | None = None) -> str:
    """计算一次生成请求的稳定指纹，用于幂等去重。

    * 客户端传了 ``Idempotency-Key`` 时以它为准（标准幂等语义，适合重试）；
    * 否则对「真正影响生成结果」的字段做规范化后 sha256：去首尾空白、
      统一布尔值、排除 ``metadata``（透传字段，不参与生成，且常含时间戳，
      纳入会导致每次请求都不同）。

        规范化很重要：用户双击时两次提交的 prompt 可能只差一个换行，仍应命中。
        ``model_variant`` 前端通常不传，这里按实际生效的配置值归一化，避免
        「传值」与「不传值」的同一请求算出两个不同指纹。
        """
    if idempotency_key and idempotency_key.strip():
        return hashlib.sha256(f"idempotency-key:{idempotency_key.strip()}".encode("utf-8")).hexdigest()

    canonical = {
        "title": (payload.title or "").strip(),
        "task_type": payload.task_type,
        "mode": payload.mode,
        "model_variant": payload.model_variant or settings.music_model,
        "inputs": {
            "prompt": (payload.inputs.prompt or "").strip(),
            "lyrics": (payload.inputs.lyrics or "").strip(),
            "instrumental": bool(payload.inputs.instrumental),
            "bpm": payload.inputs.bpm,
            "duration_seconds": payload.inputs.duration_seconds,
        },
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TooManyActiveJobs(Exception):
    """活跃生成任务数超过配置上限；由路由统一转成 HTTP 429。

    service 层不直接依赖 FastAPI，故用业务异常而非 HTTPException。
    """


class DailyQuotaExceeded(Exception):
    """今日生成额度已用完（``MUSIC_STUDIO_DAILY_LIMIT``）；由路由转成 429。

    带上 ``quota`` 快照，路由才能把重置时间写进 ``Retry-After`` 与响应头，
    前端也能据此显示「今日剩余 0 首，明天 00:00 恢复」这类友好提示。
    """

    def __init__(self, quota: DailyQuota) -> None:
        super().__init__(quota.message())
        self.quota = quota


@dataclass(frozen=True)
class CreateResult:
    """创建结果：响应体 + 是否命中幂等去重 + 创建后的配额快照。

    路由用 ``deduplicated`` 决定是否加 ``X-Deduplicated: true`` 响应头，
    用 ``quota`` 写 ``X-Daily-Remaining`` 之类的额度头；不改动
    GenerationResponse 结构，前端契约保持不变。
    """

    record: GenerationResponse
    deduplicated: bool
    quota: DailyQuota | None = None


class SongService:
    """作品 CRUD + 响应组装（所有公开 IO 方法均为 async）。

    Args:
        submit_job: 接收 song_id 的 async 回调，用于把生成任务交给后台任务。
        config / songs / stages: 均可注入，便于测试隔离数据库。
    """

    def __init__(
        self,
        *,
        submit_job: Callable[[str], Awaitable[None]],
        config: Any = None,
        songs: SongDAL | None = None,
        stages: StageDAL | None = None,
    ) -> None:
        self.config = config or settings
        self.songs = songs or SongDAL()
        self.stages = stages or StageDAL()
        self.submit_job = submit_job
        # 单进程内串行化「查重 + 插入」。asyncio.Lock 在 3.10+ 惰性绑定
        # event loop，只要不同时被两个 loop 使用即可（reset_runtime 会重建）。
        self._create_lock = asyncio.Lock()

    # ---------------- 查询 ----------------

    async def to_response(self, record: SongRecord) -> GenerationResponse:
        """数据库实体 -> 前端响应，是全项目唯一的字段映射处。

        三处需要特别留意：
        * ``musical_key`` 映射成前端的 ``key``；
        * ``model_variant`` 直接透传（响应模型是 str，兼容历史 base/turbo/shift
          与 settings.music_model 配置的模型 ID），不再做白名单改写，避免
          枚举变更导致列表接口 500；
        * ``audio_url`` 只有在本地音频文件确实存在时才生成，避免前端
          拿到 404 的播放地址；路径模板来自 constants.AUDIO_URL_TEMPLATE，
          跟着 API 版本前缀走，外部已有 URL 时原样透传。
        ``task_type`` 仍做白名单兜底，历史脏数据不会让响应模型校验失败。

        文件存在性检查走 to_thread，避免 stat 阻塞 event loop。
        """
        audio_url: str | None = None
        if record.audio_path:
            exists = await asyncio.to_thread(Path(record.audio_path).is_file)
            if exists:
                audio_url = AUDIO_URL_TEMPLATE.format(song_id=record.id)
        if audio_url is None and record.audio_url:
            audio_url = record.audio_url
        return GenerationResponse(
            id=record.id,
            title=record.title,
            task_type=record.task_type if record.task_type in ("text2music", "cover", "repaint") else "text2music",
            mode=record.mode,
            model_variant=record.model_variant or "turbo",
            status=record.status,
            prompt=record.prompt,
            lyrics=record.lyrics,
            instrumental=bool(record.instrumental),
            duration_seconds=record.duration_seconds,
            bpm=record.bpm,
            key=record.musical_key,
            time_signature=record.time_signature,
            audio_url=audio_url,
            cover_color=record.cover_color,
            cover_icon=record.cover_icon,
            cover_image_url=record.cover_image_url,
            error_message=record.error_message,
            metadata=record.metadata,
            created_at=record.created_at,
            updated_at=record.updated_at,
            current_stage=record.current_stage,
            style_prompt=record.style_prompt,
        )

    async def list_history(self, limit: int = 25) -> list[GenerationResponse]:
        """作品库列表（倒序），供 GET /v1/history 轮询。"""
        records = await self.songs.list(limit)
        return [await self.to_response(item) for item in records]

    async def get(self, song_id: str) -> SongRecord | None:
        """按 ID 取实体，供音频路由等内部使用；不存在返回 None。"""
        return await self.songs.get(song_id)

    async def get_detail(self, song_id: str) -> GenerationDetailResponse | None:
        """作品详情 = 基础响应 + 带中文标签的五阶段进度。"""
        record = await self.songs.get(song_id)
        if record is None:
            return None
        base, stage_rows = await asyncio.gather(self.to_response(record), self.stages.list(song_id))
        stages = [
            StageResponse(
                stage=item.stage,
                label=STAGE_LABELS.get(item.stage, item.stage),
                status=item.status,
                output_file=item.output_file,
                error=item.error,
                started_at=item.started_at,
                finished_at=item.finished_at,
                model=item.model,
                usage=(
                    {
                        "prompt_tokens": item.prompt_tokens,
                        "completion_tokens": item.completion_tokens,
                        "total_tokens": item.total_tokens,
                    }
                    if item.total_tokens is not None
                    else None
                ),
                metrics=item.metrics,
            )
            for item in stage_rows
        ]
        return GenerationDetailResponse(**base.model_dump(), stages=stages)

    async def active_job_counts(self, client_key: str = "") -> tuple[int, int]:
        """返回 ``(全局活跃任务数, 该客户端活跃任务数)``，供限流依赖判断。"""
        total, mine = await asyncio.gather(
            self.songs.count_active(),
            self.songs.count_active_by_client(client_key) if client_key else asyncio.sleep(0, result=0),
        )
        return int(total), int(mine)

    async def daily_quota(self) -> DailyQuota:
        """今日配额快照（上限 / 已用 / 重置时间）。

        「已用」= 今天创建的 songs 行数，包含失败记录（失败也可能已经消耗了
        LLM 额度）。不限额时也照常统计，便于 /v1/health 展示真实用量。

        日界偏移取 ``MUSIC_STUDIO_DAILY_RESET_UTC_OFFSET``（默认 8 = 东八区），
        见 services/quota.py 的实现说明。
        """
        offset = float(getattr(self.config, "daily_reset_utc_offset", 8.0))
        start, reset_at = quota_window(offset)
        used = await self.songs.count_created_since(start.isoformat())
        return DailyQuota(
            limit=int(getattr(self.config, "daily_generation_limit", 0)),
            used=used,
            reset_at=reset_at,
            offset_hours=offset,
        )

    # ---------------- 写入 ----------------

    async def create(
        self,
        payload: GenerationCreate,
        *,
        client_key: str = "",
        idempotency_key: str | None = None,
        force: bool = False,
    ) -> CreateResult:
        """创建作品并立即返回 ``queued``，不等待生成完成。

        步骤：算指纹 -> 加锁查重 -> 并发闸门 -> 每日配额 -> 生成 UUIDv7 ID ->
        推导封面主题 -> 写 songs 表 -> 预置五阶段 -> 保存用户原始输入 ->
        提交后台任务。命中去重时直接返回已有记录，不重复计费。
        ``force=True`` 跳过查重，供用户主动「再生成一次」使用。

        记录一旦写入 songs 表就进入持久化队列：即使随后进程重启，
        runtime 的队列 worker 也会扫描到这条 queued 记录并重新入队。

        提交失败时不抛异常给前端，而是把作品置为 failed 并附错误信息，
        保证「点了生成却查不到记录」的情况不会出现。

        超额时抛 ``DailyQuotaExceeded``（每日上限）或 ``TooManyActiveJobs``
        （并发上限），两者都由路由层转成 429 + ``Retry-After``：
        前者是真拒绝（明天才恢复），后者是「稍后再试」，语义不同。
        """
        fingerprint = request_fingerprint(payload, idempotency_key)

        # 锁住整个「查重 + 插入」区间：并发双击时第二个请求会等第一个
        # 插完再查，从而命中刚创建的记录，而不是各自插一条。
        async with self._create_lock:
            if not force:
                since = (
                    datetime.now(timezone.utc) - timedelta(seconds=max(0, self.config.dedupe_window_seconds))
                ).isoformat()
                existing = await self.songs.find_duplicate(fingerprint, since)
                if existing is not None:
                    logger.info("重复提交命中已有作品：%s（指纹 %s…）", existing.id, fingerprint[:8])
                    # 命中复用也回带额度快照：前端刷新后仍能显示剩余量，
                    # 且这条路径不消耗配额（used 已经在库里了，不再 +1）。
                    return CreateResult(await self.to_response(existing), True, await self.daily_quota())

            # 并发闸门必须放在幂等判断之后：命中去重的重试不会新增任务，
            # 不该因为「当前任务已满」被误拦。只有真正要新建时才检查。
            if getattr(self.config, "rate_limit_enabled", True):
                total, mine = await self.active_job_counts(client_key)
                if total >= self.config.max_active_jobs:
                    raise TooManyActiveJobs(
                        f"生成任务已达全局上限（{self.config.max_active_jobs}），请等待现有任务完成"
                    )
                if mine >= self.config.max_active_jobs_per_client:
                    raise TooManyActiveJobs(
                        f"当前客户端排队任务过多（上限 {self.config.max_active_jobs_per_client}）"
                    )

            # 每日配额：同样放在幂等判断之后（不限额时 exhausted 恒为 False）。
            # 未超额时把「即将被这条新记录占掉一格」算进去，响应头才能给出
            # 正确的剩余量；真正写库前 quota.exhausted 已经查过了。
            quota = await self.daily_quota()
            if quota.exhausted:
                logger.warning(
                    "每日生成配额已用完：%s/%s（重置于 %s）", quota.used, quota.limit, quota.reset_hint
                )
                raise DailyQuotaExceeded(quota)

            song_id = new_song_id()  # UUIDv7：时间有序，利于主键索引与作品排序
            prompt = (payload.inputs.prompt or "").strip()
            lyrics = payload.inputs.lyrics or ""
            color, icon = compute_theme(song_id)
            record = SongRecord(
                id=song_id,
                title=(payload.title or "").strip() or _fallback_title(prompt),
                task_type=payload.task_type,
                mode=payload.mode,
                model_variant=payload.model_variant or self.config.music_model,
                status=STATUS_QUEUED,
                prompt=prompt,
                lyrics=lyrics,
                instrumental=payload.inputs.instrumental,
                duration_seconds=payload.inputs.duration_seconds,
                bpm=payload.inputs.bpm,
                source_prompt=prompt,
                metadata=dict(payload.metadata),
                cover_color=color,
                cover_icon=icon,
                input_fingerprint=fingerprint,
                client_key=client_key or None,
            )
            await self.songs.create(record)
            await self.stages.initialize(song_id)
            store = ArtifactStore(Path(self.config.outputs_dir) / song_id)
            await store.save_text(prompt, USER_INPUT_FILE)
            logger.info(
                "创建作品：%s（模式=%s，时长=%s，配额 %s/%s，指纹 %s…）",
                song_id,
                record.mode,
                record.duration_seconds or "-",
                quota.used + 1,
                quota.limit or "不限",
                fingerprint[:8],
            )
            logger.debug("用户输入（前 300 字）：%s", prompt[:300].replace("\n", " "))

            try:
                await self.submit_job(song_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("后台任务提交失败：%s", song_id)
                await self.songs.update(song_id, status=STATUS_FAILED, error_message=f"任务提交失败：{exc}")
                record.status = STATUS_FAILED
                record.error_message = f"任务提交失败：{exc}"
            # 响应头里的额度按「本次创建之后」算：没超限时 remaining 已经是减过 1 的值
            quota_after = DailyQuota(
                limit=quota.limit,
                used=quota.used + 1,
                reset_at=quota.reset_at,
                offset_hours=quota.offset_hours,
            )
            return CreateResult(await self.to_response(record), False, quota_after)

    async def delete(self, song_id: str) -> bool:
        """删除作品：先清输出目录，再删数据库记录（阶段记录靠外键级联）。

        安全约束：仅当 ``task_dir`` 的父目录正是 outputs 根目录时才整目录
        删除，防止 song_id 被构造成 ``..`` 之类路径误删其他目录。
        """
        record = await self.songs.get(song_id)
        if record is None:
            return False
        outputs_root = Path(self.config.outputs_dir).resolve()
        task_dir = (outputs_root / song_id).resolve()
        if task_dir.parent == outputs_root and await asyncio.to_thread(task_dir.is_dir):
            await asyncio.to_thread(shutil.rmtree, task_dir, ignore_errors=True)
        elif record.audio_path:
            await asyncio.to_thread(Path(record.audio_path).unlink, missing_ok=True)
        return await self.songs.delete(song_id)
