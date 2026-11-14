"""service 层：五个可复用的创作阶段。

每个阶段只做「取输入 → 渲染 Prompt → 调 LLM → 校验结构 → 返回 dict」，
不写数据库、不写文件，落盘与进度由 WorkflowService 统一负责，便于单测与复用。
Prompt 模板变量严格对齐 backend/app/prompts（StrictUndefined，缺变量会直接报错）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import settings
from ..constants import STAGE_LYRICS, STAGE_MUSIC, STAGE_PLANNING, STAGE_POLISH
from ..dal import ArtifactStore
from .prompt_manager import PromptManager

logger = logging.getLogger(__name__)

SECTION_LABELS: dict[str, str] = {
    "intro": "前奏",
    "verse_1": "主歌 1",
    "verse_2": "主歌 2",
    "pre_chorus_1": "预副歌 1",
    "pre_chorus_2": "预副歌 2",
    "chorus_1": "副歌 1",
    "chorus_2": "副歌 2",
    "bridge": "桥段",
    "final_chorus": "最终副歌",
    "outro": "尾奏",
}


@dataclass
class StageContext:
    llm: Any
    prompts: PromptManager
    provider: Any
    store: ArtifactStore
    config: Any = field(default_factory=lambda: settings)
    initial_title: str = ""  # 前端提交时起的标题，供离线 mock / 兜底使用

    @property
    def music_work_dir(self) -> Path:
        return self.store.task_dir / "music"


def flatten_lyrics(value: Any) -> str:
    """把分段歌词 dict 拍平成前端可直接展示的字符串。"""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    blocks = [
        f"[{SECTION_LABELS.get(key, key)}]\n{text}"
        for key, text in value.items()
        if isinstance(text, str) and text.strip()
    ]
    return "\n\n".join(blocks)


def _original_request(stage1: dict[str, Any]) -> str:
    try:
        return str(stage1["input_summary"]["original_request"])
    except (KeyError, TypeError) as exc:
        raise ValueError("第一阶段缺少 input_summary.original_request") from exc


def run_planning(ctx: StageContext, user_input: str) -> dict[str, Any]:
    """阶段 1：音乐创作需求解析与歌曲策划。"""
    if not isinstance(user_input, str) or not user_input.strip():
        raise ValueError("用户创作需求不能为空")
    prompt = ctx.prompts.get_stage(STAGE_PLANNING, original_request=user_input.strip())
    logger.info("阶段 1：分析创作需求并生成歌曲策划")
    result = ctx.llm.chat_json(
        prompt["system_prompt"],
        prompt["user_prompt"],
        temperature=0.7,
        stage=STAGE_PLANNING,
        original_request=user_input.strip(),
    )
    if result.get("stage") != "PLANNING":
        raise ValueError("模型返回的阶段标识不正确（期望 PLANNING）")
    if not result.get("creation_spec"):
        raise ValueError("模型没有返回歌曲策划内容")
    return result


def run_lyrics(ctx: StageContext, stage1: dict[str, Any]) -> dict[str, Any]:
    """阶段 2：根据歌曲策划生成完整原创歌词。"""
    if not isinstance(stage1, dict) or not stage1.get("creation_spec"):
        raise ValueError("第一阶段缺少歌曲策划内容")
    prompt = ctx.prompts.get_stage(
        STAGE_LYRICS,
        original_request=_original_request(stage1),
        creation_spec=stage1["creation_spec"],
    )
    logger.info("阶段 2：创作原创歌词")
    result = ctx.llm.chat_json(
        prompt["system_prompt"],
        prompt["user_prompt"],
        temperature=0.8,
        stage=STAGE_LYRICS,
        original_request=_original_request(stage1),
        title=ctx.initial_title or "无名之歌",
    )
    if result.get("stage") != "LYRICS":
        raise ValueError("模型返回的阶段标识不正确（期望 LYRICS）")
    song = result.get("song")
    if not isinstance(song, dict) or not song.get("lyrics"):
        raise ValueError("模型返回的歌词为空")
    return result


def run_polish(ctx: StageContext, stage1: dict[str, Any], stage2: dict[str, Any]) -> dict[str, Any]:
    """阶段 3：歌词润色与旋律演唱设计。"""
    if not isinstance(stage1, dict) or not stage1.get("creation_spec"):
        raise ValueError("第一阶段缺少歌曲策划内容")
    if not isinstance(stage2, dict) or not isinstance(stage2.get("song"), dict):
        raise ValueError("第二阶段缺少歌曲内容")
    song = stage2["song"]
    if not song.get("lyrics"):
        raise ValueError("待润色的歌词为空")
    prompt = ctx.prompts.get_stage(
        STAGE_POLISH,
        original_request=_original_request(stage1),
        creation_spec=json.dumps(stage1["creation_spec"], ensure_ascii=False, indent=2),
        song=json.dumps(song, ensure_ascii=False, indent=2),
    )
    logger.info("阶段 3：歌词润色与旋律设计")
    result = ctx.llm.chat_json(
        prompt["system_prompt"],
        prompt["user_prompt"],
        temperature=0.7,
        stage=STAGE_POLISH,
        title=song.get("title") or "无名之歌",
        original_request=_original_request(stage1),
    )
    if result.get("stage") != "LYRICS":
        raise ValueError("模型返回的阶段标识不正确（期望 LYRICS）")
    polished = result.get("song")
    if not isinstance(polished, dict):
        raise ValueError("模型没有返回润色后的歌曲内容")
    if not polished.get("lyrics"):
        raise ValueError("润色后的歌词为空")
    if not polished.get("title"):
        raise ValueError("润色后的歌曲标题为空")
    return result


def run_music_prompt(ctx: StageContext, stage1: dict[str, Any], polished: dict[str, Any]) -> dict[str, Any]:
    """阶段 4：生成 ACE-Step 音乐制作方案。"""
    model_name = ctx.config.music_model
    creation_spec = stage1["creation_spec"]
    song = polished["song"]
    if not song.get("lyrics"):
        raise ValueError("最终歌词为空，无法生成音乐方案")
    prompt = ctx.prompts.get_stage(
        STAGE_MUSIC,
        target_model=model_name,
        original_request=_original_request(stage1),
        music_spec=json.dumps(creation_spec.get("music", {}), ensure_ascii=False, indent=2),
        lyrics_direction=json.dumps(creation_spec.get("lyrics_direction", {}), ensure_ascii=False, indent=2),
        title=song["title"],
        final_lyrics=json.dumps(song["lyrics"], ensure_ascii=False, indent=2),
        vocal_melody_design=json.dumps(song.get("vocal_melody_design", {}), ensure_ascii=False, indent=2),
    )
    logger.info("阶段 4：生成音乐制作方案")
    result = ctx.llm.chat_json(
        prompt["system_prompt"],
        prompt["user_prompt"],
        temperature=0.7,
        stage=STAGE_MUSIC,
        target_model=model_name,
        title=song["title"],
    )
    if result.get("target_model") != model_name:
        raise ValueError("目标模型不匹配")
    if result.get("song", {}).get("title") != song["title"]:
        raise ValueError("模型意外修改了歌曲标题")
    return result


def run_final_music(ctx: StageContext, plan: dict[str, Any], *, instrumental: bool) -> dict[str, Any]:
    """阶段 5：按制作方案调用音乐生成接口并下载音频。"""
    title = plan["song"]["title"]
    style = plan["music_prompts"]["style"]
    duration = int(plan["ace_music_settings"]["duration_seconds"])
    lyrics = "[Instrumental]" if instrumental else plan["song"]["lyrics"]
    logger.info("阶段 5：生成歌曲《%s》（%s 秒）", title, duration)
    result = ctx.provider.generate(
        prompt=style,
        lyrics=lyrics,
        duration=duration,
        work_dir=ctx.music_work_dir,
    )
    return {**result, "title": title, "prompt": style, "duration": duration}
