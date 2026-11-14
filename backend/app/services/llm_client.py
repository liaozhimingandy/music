"""service 层：OpenAI 兼容 LLM 客户端（httpx 实现，无 SDK 依赖）。

- `chat()` 返回纯文本；`chat_json()` 解析 JSON 并容忍 ```json 代码块。
- `stage` / 其他 hints 供离线 MockLLMClient 使用，真实客户端忽略。
- 提供 MockLLMClient，配合 MUSIC_STUDIO_MOCK=true 可不联网跑通全流程。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from ..config import settings
from ..constants import (
    STAGE_LYRICS,
    STAGE_MUSIC,
    STAGE_PLANNING,
    STAGE_POLISH,
)

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def strip_code_fence(content: str) -> str:
    return _CODE_FENCE.sub("", content.strip()).strip()


class LLMClientProtocol(Protocol):
    model: str

    def chat_json(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> dict[str, Any]: ...


class LLMClient:
    def __init__(self, config: Any = None) -> None:
        cfg = config or settings
        self.model = cfg.llm_model
        self.max_retries = max(0, int(cfg.llm_max_retries))
        base_url = cfg.llm_base_url.rstrip("/") + "/"
        headers = {"Content-Type": "application/json"}
        if cfg.llm_api_key:
            headers["Authorization"] = f"Bearer {cfg.llm_api_key}"

        verify: bool | str = True
        if cfg.llm_ca_cert and Path(cfg.llm_ca_cert).is_file():
            verify = cfg.llm_ca_cert

        timeout = httpx.Timeout(
            connect=cfg.llm_connect_timeout,
            read=cfg.llm_read_timeout,
            write=cfg.llm_write_timeout,
            pool=30.0,
        )
        self.client = httpx.Client(base_url=base_url, headers=headers, timeout=timeout, verify=verify)

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.7,
        model: str | None = None,
        stage: str | None = None,  # noqa: ARG002 - 仅 Mock 使用
        **hints: Any,  # noqa: ARG002 - 仅 Mock 使用
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "stream": False,
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post("chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                return str(data["choices"][0]["message"]["content"] or "")
            except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败：{last_error}") from last_error

    def chat_json(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> dict[str, Any]:
        content = self.chat(system_prompt, user_prompt, **kwargs)
        try:
            result = json.loads(strip_code_fence(content))
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型返回内容不是合法 JSON（{kwargs.get('stage') or 'unknown'}）：{content[:300]}") from exc
        if not isinstance(result, dict):
            raise ValueError(f"模型返回 JSON 不是对象：{type(result).__name__}")
        return result


class MockLLMClient:
    """离线演示用：按阶段返回满足校验的最小合法结构，不发起任何网络请求。"""

    model = "mock-llm"

    def chat(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,
        *,
        temperature: float = 0.7,  # noqa: ARG002
        model: str | None = None,  # noqa: ARG002
        stage: str | None = None,
        **hints: Any,
    ) -> str:
        return json.dumps(self.chat_json(system_prompt, user_prompt, stage=stage, **hints), ensure_ascii=False)

    def chat_json(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,
        *,
        stage: str | None = None,
        **hints: Any,
    ) -> dict[str, Any]:
        seed = str(hints.get("original_request") or user_prompt).strip()[:120]
        title = str(hints.get("title") or "无名之歌")
        lyrics_map = {
            "intro": "风轻轻吹过窗台",
            "verse_1": f"{seed}\n把没说完的话收进口袋",
            "chorus_1": "就让这首歌 替我留下来\n在每一次回头时 都还在",
            "bridge": "时间走得很慢 慢到能听见",
            "final_chorus": "就让这首歌 替我留下来",
        }
        song = {
            "title": title,
            "creative_concept": seed,
            "lyrics": lyrics_map,
            "core_hook": "就让这首歌 替我留下来",
            "writing_notes": ["离线演示数据"],
            "vocal_melody_design": {"overall_direction": "自然、有旋律感的中文演唱"},
        }
        if stage == STAGE_PLANNING:
            return {
                "schema_version": "1.0",
                "stage": "PLANNING",
                "status": "ready",
                "input_summary": {"original_request": seed, "interpreted_intent": seed},
                "creation_spec": {
                    "basic": {"title": {"value": title}},
                    "music": {"genre": ["华语流行"], "mood": ["温柔"], "tempo": {"bpm": 88}},
                    "lyrics_direction": {"core_message": seed},
                    "ai_music_prompt": {"positive_prompt": seed},
                },
                "assumptions": [],
                "quality_check": {"issues": []},
                "next_step": {"action": "GENERATE_LYRICS"},
            }
        if stage == STAGE_LYRICS:
            return {"schema_version": "1.0", "stage": "LYRICS", "status": "completed", "song": song}
        if stage == STAGE_POLISH:
            polished = {**song, "lyrics": {**lyrics_map, "verse_1": f"{seed}\n把没说完的话 轻轻收进口袋"}}
            return {"schema_version": "1.1", "stage": "LYRICS", "status": "completed", "song": polished}
        if stage == STAGE_MUSIC:
            return {
                "schema_version": "1.3",
                "stage": "MUSIC_PROMPT",
                "status": "completed",
                "target_model": hints.get("target_model") or settings.music_model,
                "song": {
                    "title": title,
                    "lyrics": "[Verse 1]\n" + "\n".join(lyrics_map.values()),
                },
                "music_analysis": {"genre": ["华语流行"], "mood": ["温柔"], "vocal_direction": "温暖男声"},
                "music_prompts": {"style": "Mandarin pop ballad, warm male vocal, soft piano and strings, 88 BPM"},
                "ace_music_settings": {"duration_seconds": int(hints.get("duration") or 60)},
                "generation_notes": ["离线演示数据"],
                "quality_check": {"lyrics_preserved": True, "issues": []},
                "next_step": {"action": "READY_FOR_MUSIC_GENERATION"},
            }
        return {}


def build_llm_client(config: Any = None) -> LLMClientProtocol:
    cfg = config or settings
    if cfg.mock_mode or not cfg.llm_api_key:
        return MockLLMClient()
    return LLMClient(cfg)
