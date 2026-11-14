"""service 层：五个可复用的创作阶段（全异步）。

每个阶段只做「取输入 → 渲染 Prompt → 调 LLM → 校验结构 → 返回 dict」，
不写数据库、不写文件，落盘与进度由 WorkflowService 统一负责，便于单测与复用。
所有 IO（模板渲染、LLM 请求、音乐生成）都 ``await``，等待外部服务期间
event loop 仍可处理前端的轮询请求。

五个阶段（顺序见 constants.STAGE_ORDER）：

1. ``planning``  解析需求，产出 creation_spec
2. ``lyrics``    产出分段歌词
3. ``polish``    润色歌词 + 演唱设计
4. ``music``     产出 ACE-Step 制作方案（style / 时长）
5. ``music_generation``（run_final_music）调用音乐生成接口并下载音频

Prompt 模板变量严格对齐 backend/app/prompts（StrictUndefined，缺变量会直接报错）。
**维护提示**：新增模板变量时，要同时改对应 user.md（由用户维护，不可擅自修改）
与本文件的 get_stage 调用参数，否则运行期会立刻抛 UndefinedError。
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

# 分段歌词的内部键 -> 中文段落标记，用于把 dict 拍平成可展示的歌词文本。
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
    """五个阶段共享的依赖容器，由 WorkflowService 每次任务构造一次。

    把 LLM / 模板 / 音乐提供方 / 产物目录都收进上下文，阶段函数就能保持
    纯函数形态：同样输入 + 同样上下文 = 同样输出，测试可直接注入 mock。
    """

    llm: Any                         # 满足 LLMClientProtocol（async）的客户端
    prompts: PromptManager           # 只读模板加载器（async）
    provider: Any                    # 满足 async generate(...) 的音乐提供方
    store: ArtifactStore             # 该作品的输出目录（async）
    config: Any = field(default_factory=lambda: settings)
    initial_title: str = ""  # 前端提交时起的标题，供离线 mock / 兜底使用

    @property
    def music_work_dir(self) -> Path:
        """音乐阶段独立子目录，存放 music_task.json 与最终音频。"""
        return self.store.task_dir / "music"


def flatten_lyrics(value: Any) -> str:
    """把分段歌词 dict 拍平成前端可直接展示的字符串。

    纯计算、无 IO，保持同步。输入可能是字符串（已是最终歌词）或
    ``{"verse_1": "...", ...}``；未知段落键原样作为标记，空段落跳过。
    字符串以 ``[段落名]`` 开头、段落间空行分隔，正好符合前端歌词展示格式。
    """
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
    """从阶段一结果里取用户原始描述；缺失时给出可定位的报错。"""
    try:
        return str(stage1["input_summary"]["original_request"])
    except (KeyError, TypeError) as exc:
        raise ValueError("第一阶段缺少 input_summary.original_request") from exc


async def run_planning(ctx: StageContext, user_input: str) -> dict[str, Any]:
    """阶段 1：音乐创作需求解析与歌曲策划。

    模板变量：``original_request``。校验 stage 标识为 PLANNING 且存在
    creation_spec；后续所有阶段都依赖这份策划。
    """
    if not isinstance(user_input, str) or not user_input.strip():
        raise ValueError("用户创作需求不能为空")
    prompt = await ctx.prompts.get_stage(STAGE_PLANNING, original_request=user_input.strip())
    logger.info("阶段 1：分析创作需求并生成歌曲策划")
    result = await ctx.llm.chat_json(
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


async def run_lyrics(ctx: StageContext, stage1: dict[str, Any]) -> dict[str, Any]:
    """阶段 2：根据歌曲策划生成完整原创歌词。

    模板变量：``original_request`` / ``creation_spec``（直接传对象给模板）。
    校验阶段标识与 ``song.lyrics`` 非空。
    """
    if not isinstance(stage1, dict) or not stage1.get("creation_spec"):
        raise ValueError("第一阶段缺少歌曲策划内容")
    prompt = await ctx.prompts.get_stage(
        STAGE_LYRICS,
        original_request=_original_request(stage1),
        creation_spec=stage1["creation_spec"],
    )
    logger.info("阶段 2：创作原创歌词")
    result = await ctx.llm.chat_json(
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


async def run_polish(ctx: StageContext, stage1: dict[str, Any], stage2: dict[str, Any]) -> dict[str, Any]:
    """阶段 3：歌词润色与旋律演唱设计。

    模板变量：``original_request`` / ``creation_spec`` / ``song``，后两者
    先 json.dumps 成结构化文本再交给模板。校验润色结果必须带 lyrics 与
    title——这两个字段会覆盖前端提交的初值写回作品表。
    """
    if not isinstance(stage1, dict) or not stage1.get("creation_spec"):
        raise ValueError("第一阶段缺少歌曲策划内容")
    if not isinstance(stage2, dict) or not isinstance(stage2.get("song"), dict):
        raise ValueError("第二阶段缺少歌曲内容")
    song = stage2["song"]
    if not song.get("lyrics"):
        raise ValueError("待润色的歌词为空")
    prompt = await ctx.prompts.get_stage(
        STAGE_POLISH,
        original_request=_original_request(stage1),
        creation_spec=json.dumps(stage1["creation_spec"], ensure_ascii=False, indent=2),
        song=json.dumps(song, ensure_ascii=False, indent=2),
    )
    logger.info("阶段 3：歌词润色与旋律设计")
    result = await ctx.llm.chat_json(
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


async def run_music_prompt(ctx: StageContext, stage1: dict[str, Any], polished: dict[str, Any]) -> dict[str, Any]:
    """阶段 4：生成 ACE-Step 音乐制作方案。

    模板变量：target_model / original_request / music_spec / lyrics_direction /
    title / final_lyrics / vocal_melody_design。校验目标模型与标题未被模型
    擅自改动，否则最终音频会与作品信息对不上。
    """
    model_name = ctx.config.music_model
    creation_spec = stage1["creation_spec"]
    song = polished["song"]
    if not song.get("lyrics"):
        raise ValueError("最终歌词为空，无法生成音乐方案")
    prompt = await ctx.prompts.get_stage(
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
    result = await ctx.llm.chat_json(
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


async def run_final_music(ctx: StageContext, plan: dict[str, Any], *, instrumental: bool) -> dict[str, Any]:
    """阶段 5：按制作方案调用音乐生成接口并下载音频。

    纯音乐时把歌词固定为 ``[Instrumental]``；其余情况用方案里的最终歌词。
    返回 provider 结果并补上 title / prompt / duration，供 WorkflowService 落库。
    """
    title = plan["song"]["title"]
    style = plan["music_prompts"]["style"]
    duration = int(plan["ace_music_settings"]["duration_seconds"])
    lyrics = "[Instrumental]" if instrumental else plan["song"]["lyrics"]
    logger.info("阶段 5：生成歌曲《%s》（%s 秒）", title, duration)
    result = await ctx.provider.generate(
        prompt=style,
        lyrics=lyrics,
        duration=duration,
        work_dir=ctx.music_work_dir,
    )
    return {**result, "title": title, "prompt": style, "duration": duration}
