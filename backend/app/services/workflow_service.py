"""service 层：MusicFlow 编排器（全异步）。

把五个阶段串成后台 asyncio 任务：逐阶段落盘 + 写 SQLite 进度 + 更新作品字段。
任一阶段失败都会把作品置为 failed 并保留已完成阶段的产物，便于续跑。

前端体验链路：POST 返回 ``queued`` -> 本服务置 ``processing`` 并逐阶段
更新 ``current_stage`` -> 全部完成置 ``ready`` 并写 audio_path；
前端每 2 秒轮询 /v1/history 即可刷新状态与进度。

续跑约定：阶段产物 JSON 存在即视为该阶段已完成（``_execute`` 直接复用），
产物损坏则重新执行该阶段。进程重启后由 ``runtime`` 的队列 worker 扫描
``queued/processing`` 记录重新入队，本服务便从这里继续；因此清理
``data/outputs/<song_id>/`` 会触发重跑，而保留目录则跳过已完成阶段。

所有数据库与文件访问都 ``await``，等待 LLM / 远端音乐生成期间不会阻塞
event loop；同一时刻能跑几个作品由 runtime 的 asyncio.Semaphore 控制。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import settings
from ..constants import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_READY,
    STAGE_LYRICS,
    STAGE_MUSIC_GENERATION,
    STAGE_MUSIC_PROMPT,
    STAGE_OUTPUTS,
    STAGE_PLANNING,
    STAGE_POLISH,
    USER_INPUT_FILE,
)
from ..dal import ArtifactStore, SongDAL, StageDAL
from .llm_client import build_llm_client
from .music_provider import build_music_provider
from .prompt_manager import PromptManager
from .stage_steps import (
    StageContext,
    StageTelemetry,
    flatten_lyrics,
    run_final_music,
    run_lyrics,
    run_music_prompt,
    run_planning,
    run_polish,
)

logger = logging.getLogger(__name__)


def _format_usage(usage: dict[str, int] | None) -> str:
    """把 OpenAI 风格 usage 压成一行，供阶段完成日志直接打印。

    没有用量时返回「（无）」（音乐阶段与 mock 客户端都没有 token 概念）。
    """
    if not usage:
        return "（无）"
    return "输入 {prompt_tokens} / 输出 {completion_tokens} / 合计 {total_tokens}".format(
        prompt_tokens=usage.get("prompt_tokens", "-"),
        completion_tokens=usage.get("completion_tokens", "-"),
        total_tokens=usage.get("total_tokens", "-"),
    )


def _extract_bpm(stage1: dict[str, Any]) -> int | None:
    """从策划结果里尽量提取 BPM（纯计算，保持同步）。

    模型返回格式不固定：tempo 可能是 dict（含 bpm / value / range /
    description）、数字，或带数字的字符串（如 "88-92 BPM"）。这里按顺序
    尝试并回退到正则取 2~3 位数字，取不到就返回 None（前端显示为空）。
    """
    tempo = (stage1.get("creation_spec") or {}).get("music", {}).get("tempo")
    if isinstance(tempo, dict):
        for key in ("bpm", "value", "range", "description"):
            value = tempo.get(key)
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                match = re.search(r"\d{2,3}", value)
                if match:
                    return int(match.group())
    elif isinstance(tempo, (int, float)):
        return int(tempo)
    elif isinstance(tempo, str):
        match = re.search(r"\d{2,3}", tempo)
        if match:
            return int(match.group())
    return None


class WorkflowService:
    """五阶段流程的执行器；由 runtime 的 ``asyncio.Task`` 调用 ``await run(song_id)``。

    所有依赖（DAL / LLM / 音乐提供方 / 模板）都可注入，冒烟测试正是用
    mock 客户端 + 临时目录跑通全流程而不联网。
    """

    def __init__(
        self,
        *,
        config: Any = None,
        songs: SongDAL | None = None,
        stages: StageDAL | None = None,
        llm: Any = None,
        provider: Any = None,
        prompts: PromptManager | None = None,
    ) -> None:
        self.config = config or settings
        self.songs = songs or SongDAL()
        self.stages = stages or StageDAL()
        self.llm = llm or build_llm_client(self.config)
        self.provider = provider or build_music_provider(self.config)
        self.prompts = prompts or PromptManager(self.config.prompts_dir)

    async def aclose(self) -> None:
        """释放 LLM 的 httpx 连接池；由 runtime.shutdown() 在应用关闭时调用。"""
        closer = getattr(self.llm, "aclose", None)
        if closer is not None:
            await closer()

    async def run(self, song_id: str) -> None:
        """执行一首歌的完整流程；异常会先落库为 failed 再向上抛出。

        阶段之间还会把中间结果写回 songs 表：阶段三后更新最终标题与歌词，
        阶段四后更新风格描述、时长、BPM 与 metadata，阶段五后写音频路径
        并置为 ready。这样即使中途失败，作品库里也能看到已产出的内容。
        """
        song = await self.songs.get(song_id)
        if song is None:
            raise KeyError(f"作品不存在：{song_id}")

        store = ArtifactStore(Path(self.config.outputs_dir) / song_id, resume=True)
        # 优先用落盘的原始输入（未被后续扩写覆盖），保证续跑时输入一致。
        user_input = song.prompt or song.source_prompt or song.title or ""
        if await store.exists(USER_INPUT_FILE):
            user_input = await store.load_text(USER_INPUT_FILE)
        else:
            await store.save_text(user_input, USER_INPUT_FILE)

        ctx = StageContext(
            llm=self.llm,
            prompts=self.prompts,
            provider=self.provider,
            store=store,
            config=self.config,
            initial_title=song.title or "",
        )
        # 本次任务实际使用的 LLM 模型写进 metadata：song_stages.model 记的是
        # 「每个阶段那次调用返回的模型」，这里记「这次任务整体用的哪个」，
        # 换模型后一眼能看出某首歌是哪个模型产出的（阶段复用时不覆盖）。
        llm_model = getattr(self.llm, "model", None) or getattr(self.config, "llm_model", None)
        await self.songs.update(song_id, metadata={**(song.metadata or {}), "llm_model": llm_model})
        logger.info(
            "开始 MusicFlow 任务：%s（输入 %s 字，LLM=%s，断点续跑=%s）",
            song_id,
            len(user_input),
            llm_model,
            bool(getattr(self.config, "resume_enabled", True)),
        )
        await self.songs.update(
            song_id, status=STATUS_PROCESSING, current_stage=STAGE_PLANNING, error_message=None
        )

        try:
            # --- 阶段 1~3：策划 -> 歌词 -> 润色 ---
            # lambda 返回 coroutine，_execute 内部 await func() 即可。
            stage1 = await self._execute(
                ctx, song_id, STAGE_PLANNING,
                lambda: run_planning(ctx, user_input), STAGE_LYRICS,
            )
            stage2 = await self._execute(
                ctx, song_id, STAGE_LYRICS,
                lambda: run_lyrics(ctx, stage1), STAGE_POLISH,
            )
            polished = await self._execute(
                ctx, song_id, STAGE_POLISH,
                lambda: run_polish(ctx, stage1, stage2), STAGE_MUSIC_PROMPT,
            )

            # 润色结果为准，覆盖前端提交的初始标题 / 歌词。
            final_song = polished["song"]
            await self.songs.update(
                song_id,
                title=final_song.get("title") or song.title,
                lyrics=flatten_lyrics(final_song.get("lyrics")),
            )

            # --- 阶段 4：音乐制作方案，回填展示字段 ---
            plan = await self._execute(
                ctx, song_id, STAGE_MUSIC_PROMPT,
                lambda: run_music_prompt(ctx, stage1, polished), STAGE_MUSIC_GENERATION,
            )
            analysis = plan.get("music_analysis") or {}
            metadata = {
                **song.metadata,
                "source": "musicflow",
                "llm_model": llm_model,
                "target_model": plan.get("target_model", self.config.music_model),
                "genre": analysis.get("genre") or [],
                "mood": analysis.get("mood") or [],
            }
            await self.songs.update(
                song_id,
                style_prompt=plan["music_prompts"]["style"],
                duration_seconds=int(plan["ace_music_settings"]["duration_seconds"]),
                bpm=_extract_bpm(stage1),
                metadata=metadata,
            )

            # --- 阶段 5：生成音频，作品转为 ready ---
            result = await self._execute(
                ctx, song_id, STAGE_MUSIC_GENERATION,
                lambda: run_final_music(ctx, plan, instrumental=song.instrumental), None,
            )
            metadata["remote_audio_url"] = result.get("audio_url")
            await self.songs.update(
                song_id,
                status=STATUS_READY,
                current_stage=None,
                error_message=None,
                audio_path=result.get("audio_path"),
                provider_task_id=result.get("task_id"),
                metadata=metadata,
            )
            logger.info("MusicFlow 任务完成：%s", song_id)
        except Exception as exc:  # noqa: BLE001 - 统一落库后向上抛给任务日志
            logger.exception("MusicFlow 任务失败：%s", song_id)
            await self.songs.update(song_id, status=STATUS_FAILED, current_stage=None, error_message=str(exc))
            raise

    async def _execute(
        self,
        ctx: StageContext,
        song_id: str,
        stage: str,
        func: Callable[[], Awaitable[tuple[dict[str, Any], StageTelemetry]]],
        next_stage: str | None,
    ) -> dict[str, Any]:
        """执行单个阶段，并负责断点续跑、进度落盘、遥测记录与异常标记。

        流程：产物已存在且可解析 -> 直接复用；否则标记 running -> ``await``
        阶段函数 -> 原子写产物 -> 标记 completed（带 model / token / metrics）
        -> 推进 current_stage。耗时统一在这里用 ``time.monotonic`` 计量，
        与阶段返回的 metrics 合并成 ``elapsed_ms`` 写入遥测列。
        任一异常先标记该阶段 failed（也记录耗时）再抛出，由 ``run`` 统一置
        作品 failed。
        """
        output = STAGE_OUTPUTS[stage]
        # MUSIC_STUDIO_RESUME=false 时忽略已有产物，强制按当前配置重跑
        # （换了 LLM 模型想听新效果、或产物写了一半看起来正常但内容不对）。
        if getattr(self.config, "resume_enabled", True) and await ctx.store.exists(output):
            try:
                cached = await ctx.store.load_json(output)
                if isinstance(cached, dict):
                    # 复用旧产物：调用 reuse 而不是 complete，避免把上次记录的
                    # 模型 / token 覆盖成本次运行的空值（详见 StageDAL.reuse）。
                    previous_model = await self.stages.reuse(song_id, stage)
                    logger.warning(
                        "阶段 %s 复用已落盘产物 %s，跳过执行（模型沿用上次记录的 %s）",
                        stage,
                        output,
                        previous_model or "（空）",
                    )
                    logger.debug("复用产物内容大小：%s -> %d 个顶层键", output, len(cached))
                    await self._advance(song_id, next_stage)
                    return cached
            except (ValueError, OSError, json.JSONDecodeError):
                # 半截 / 损坏的 JSON 不能信任，重新跑这一阶段。
                logger.warning("阶段产物损坏，重新执行：%s", stage)

        logger.info("开始执行阶段：%s", stage)
        await self.stages.start(song_id, stage)
        await self._advance(song_id, stage)
        started = time.monotonic()
        try:
            result, telemetry = await func()
            elapsed_ms = round((time.monotonic() - started) * 1000)
            await ctx.store.save_json(result, output)
            await self.stages.complete(
                song_id,
                stage,
                model=telemetry.model,
                usage=telemetry.usage,
                metrics={**telemetry.metrics, "elapsed_ms": elapsed_ms},
            )
            # 打完这一行，日志里就能直接对上「哪个阶段用了哪个模型、多少 token」，
            # 不用再翻数据库——这正是此前排查「模型没记录」时缺的信息。
            logger.info(
                "阶段完成：%s ｜ 模型=%s ｜ tokens=%s ｜ 耗时=%.1fs",
                stage,
                telemetry.model or "（未返回模型名）",
                _format_usage(telemetry.usage),
                elapsed_ms / 1000,
            )
            await self._advance(song_id, next_stage)
            return result
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = round((time.monotonic() - started) * 1000)
            await self.stages.fail(song_id, stage, str(exc), metrics={"elapsed_ms": elapsed_ms})
            logger.error("阶段执行失败：%s，耗时 %.1fs，原因：%s", stage, elapsed_ms / 1000, exc)
            raise

    async def _advance(self, song_id: str, stage: str | None) -> None:
        """更新作品当前阶段；传 None 表示全部完成，前端不再显示进度。"""
        await self.songs.update(song_id, current_stage=stage)
