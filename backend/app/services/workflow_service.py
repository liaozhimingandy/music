"""service 层：MusicFlow 编排器。

把五个阶段串成后台任务：逐阶段落盘 + 写 SQLite 进度 + 更新作品字段。
任一阶段失败都会把作品置为 failed 并保留已完成阶段的产物，便于续跑。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from ..config import settings
from ..constants import (
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_READY,
    STAGE_LYRICS,
    STAGE_MUSIC,
    STAGE_MUSIC_GENERATION,
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
    flatten_lyrics,
    run_final_music,
    run_lyrics,
    run_music_prompt,
    run_planning,
    run_polish,
)

logger = logging.getLogger(__name__)


def _extract_bpm(stage1: dict[str, Any]) -> int | None:
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

    def run(self, song_id: str) -> None:
        song = self.songs.get(song_id)
        if song is None:
            raise KeyError(f"作品不存在：{song_id}")

        store = ArtifactStore(Path(self.config.outputs_dir) / song_id, resume=True)
        user_input = song.prompt or song.source_prompt or song.title or ""
        if store.exists(USER_INPUT_FILE):
            user_input = store.load_text(USER_INPUT_FILE)
        else:
            store.save_text(user_input, USER_INPUT_FILE)

        ctx = StageContext(
            llm=self.llm,
            prompts=self.prompts,
            provider=self.provider,
            store=store,
            config=self.config,
            initial_title=song.title or "",
        )
        logger.info("开始 MusicFlow 任务：%s（输入 %s 字）", song_id, len(user_input))
        self.songs.update(
            song_id, status=STATUS_PROCESSING, current_stage=STAGE_PLANNING, error_message=None
        )

        try:
            stage1 = self._execute(
                ctx, song_id, STAGE_PLANNING,
                lambda: run_planning(ctx, user_input), STAGE_LYRICS,
            )
            stage2 = self._execute(
                ctx, song_id, STAGE_LYRICS,
                lambda: run_lyrics(ctx, stage1), STAGE_POLISH,
            )
            polished = self._execute(
                ctx, song_id, STAGE_POLISH,
                lambda: run_polish(ctx, stage1, stage2), STAGE_MUSIC,
            )

            final_song = polished["song"]
            self.songs.update(
                song_id,
                title=final_song.get("title") or song.title,
                lyrics=flatten_lyrics(final_song.get("lyrics")),
            )

            plan = self._execute(
                ctx, song_id, STAGE_MUSIC,
                lambda: run_music_prompt(ctx, stage1, polished), STAGE_MUSIC_GENERATION,
            )
            analysis = plan.get("music_analysis") or {}
            metadata = {
                **song.metadata,
                "source": "musicflow",
                "target_model": plan.get("target_model", self.config.music_model),
                "genre": analysis.get("genre") or [],
                "mood": analysis.get("mood") or [],
            }
            self.songs.update(
                song_id,
                style_prompt=plan["music_prompts"]["style"],
                duration_seconds=int(plan["ace_music_settings"]["duration_seconds"]),
                bpm=_extract_bpm(stage1),
                metadata=metadata,
            )

            result = self._execute(
                ctx, song_id, STAGE_MUSIC_GENERATION,
                lambda: run_final_music(ctx, plan, instrumental=song.instrumental), None,
            )
            metadata["remote_audio_url"] = result.get("audio_url")
            self.songs.update(
                song_id,
                status=STATUS_READY,
                current_stage=None,
                error_message=None,
                audio_path=result.get("audio_path"),
                provider_task_id=result.get("task_id"),
                metadata=metadata,
            )
            logger.info("MusicFlow 任务完成：%s", song_id)
        except Exception as exc:  # noqa: BLE001 - 统一落库后向上抛给线程日志
            logger.exception("MusicFlow 任务失败：%s", song_id)
            self.songs.update(song_id, status=STATUS_FAILED, current_stage=None, error_message=str(exc))
            raise

    def _execute(
        self,
        ctx: StageContext,
        song_id: str,
        stage: str,
        func: Callable[[], dict[str, Any]],
        next_stage: str | None,
    ) -> dict[str, Any]:
        output = STAGE_OUTPUTS[stage]
        if ctx.store.exists(output):
            try:
                cached = ctx.store.load_json(output)
                if isinstance(cached, dict):
                    logger.info("阶段已完成，跳过：%s", stage)
                    self.stages.complete(song_id, stage)
                    self._advance(song_id, next_stage)
                    return cached
            except (ValueError, OSError, json.JSONDecodeError):
                logger.warning("阶段产物损坏，重新执行：%s", stage)

        logger.info("开始执行阶段：%s", stage)
        self.stages.start(song_id, stage)
        self._advance(song_id, stage)
        try:
            result = func()
            ctx.store.save_json(result, output)
            self.stages.complete(song_id, stage)
            self._advance(song_id, next_stage)
            return result
        except Exception as exc:  # noqa: BLE001
            self.stages.fail(song_id, stage, str(exc))
            logger.error("阶段执行失败：%s，原因：%s", stage, exc)
            raise

    def _advance(self, song_id: str, stage: str | None) -> None:
        self.songs.update(song_id, current_stage=stage)
