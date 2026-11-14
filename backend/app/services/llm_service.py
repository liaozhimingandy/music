"""service 层：前端「AI 扩写描述 / 写词 / 起标题」三个轻量 LLM 任务。

与五阶段流程解耦：这里使用代码内的简短系统提示词，
不改动 backend/app/prompts 下的任何模板。
未配置 API Key 或开启 MUSIC_STUDIO_MOCK 时使用本地兜底，保证界面可用。
"""

from __future__ import annotations

import re
from typing import Any

from ..config import settings
from ..models import LLMTaskRequest, LLMTaskResponse
from .llm_client import build_llm_client, strip_code_fence

SYSTEM_PROMPTS: dict[str, str] = {
    "prompt": (
        "你是资深音乐制作人。把用户的简短灵感扩写成一段中文歌曲描述，"
        "涵盖曲风、情绪、人声、乐器与编曲层次，3-5 句话，不超过 500 字，"
        "不要提问、不要分点、不要 Markdown，只返回描述正文。"
    ),
    "lyrics": (
        "你是华语作词人。根据歌曲描述创作完整原创中文歌词，"
        "使用 [主歌]、[预副歌]、[副歌]、[桥段] 等段落标记，"
        "副歌要有记忆点，不要复制现有歌曲，只返回歌词正文。"
    ),
    "title": "你是唱片企划。为歌曲概念起一个简短中文标题，不超过 6 个字，只返回标题本身，不要引号或解释。",
}

TEMPERATURES: dict[str, float] = {"prompt": 0.7, "lyrics": 0.8, "title": 0.6}


def _local_fallback(payload: LLMTaskRequest) -> str:
    seed = " ".join(payload.seed_prompt.split())
    if payload.task == "title":
        cleaned = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9 ]", "", seed).strip()
        return (cleaned[:6] or "无名之歌")[:6]
    if payload.task == "lyrics":
        if payload.instrumental:
            return "[Instrumental]"
        return (
            f"[主歌]\n{seed[:36]}\n把没说完的话 收进傍晚的风\n\n"
            "[副歌]\n就让这首歌 替我留下来\n在每一次回头时 都还在\n\n"
            "[桥段]\n时间走得很慢 慢到能听见\n\n[副歌]\n就让这首歌 替我留下来"
        )
    instrumental = "纯音乐、无人声，" if payload.instrumental else ""
    return (
        f"{seed[:160]}。整体是一首{instrumental}情绪克制而温暖的华语作品，"
        "以钢琴与木吉他铺底，弦乐在中段渐次加入，人声靠前、咬字自然；"
        "主歌保持叙述感，副歌通过和声与鼓组打开层次，尾奏回到安静的单件乐器，保留真实的动态起伏。"
    )[:500]


class LLMService:
    def __init__(self, *, llm: Any = None, config: Any = None) -> None:
        self.config = config or settings
        self.llm = llm or build_llm_client(self.config)

    def run(self, payload: LLMTaskRequest) -> LLMTaskResponse:
        if self.config.mock_mode or not self.config.llm_api_key:
            return LLMTaskResponse(
                task=payload.task,
                output=_local_fallback(payload),
                provider="local",
                metadata={"mode": "offline"},
            )

        output = strip_code_fence(
            self.llm.chat(
                SYSTEM_PROMPTS[payload.task],
                payload.seed_prompt,
                temperature=TEMPERATURES[payload.task],
            )
        ).strip().strip('"“”')
        if payload.task == "lyrics" and payload.instrumental:
            output = "[Instrumental]"
        return LLMTaskResponse(
            task=payload.task,
            output=output,
            provider=self.llm.model,
            metadata={"model": self.llm.model},
        )
