"""service 层：OpenAI 兼容 LLM 客户端（httpx.AsyncClient，无 SDK 依赖）。

* ``chat()`` 返回纯文本；``chat_json()`` 解析 JSON 并容忍 ```json 代码块。
* ``stage`` / 其他 hints 供离线 MockLLMClient 使用，真实客户端忽略。
* 提供 MockLLMClient，配合 ``MUSIC_STUDIO_MOCK=true`` 可不联网跑通全流程。

全异步：网络请求用 ``await``，重试退改用 ``asyncio.sleep``，因此一个作品
等待模型响应时不会阻塞 event loop 上的其它请求（前端轮询照常返回）。
长连接 ``AsyncClient`` 由 WorkflowService.aclose() 在应用关闭时释放。

配置来源为 backend/app/.env（见 .env.example）：LLM_BASE_URL / LLM_MODEL /
LLM_API_KEY / LLM_CA_CERT 与三个超时值。CA 证书是可选的，用于企业代理
场景下的自签名证书；文件不存在时回退到系统默认证书校验。
"""

from __future__ import annotations

import asyncio
import json
import re
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

# 模型偶尔会把 JSON 包在 ```json ... ``` 里，这里只剥首尾围栏。
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def strip_code_fence(content: str) -> str:
    """去掉模型输出首尾的 Markdown 代码围栏，返回可直接解析的文本。"""
    return _CODE_FENCE.sub("", content.strip()).strip()


class LLMClientProtocol(Protocol):
    """阶段函数依赖的最小接口；真实 / Mock 客户端都可无缝替换。

    维护提示：所有方法都是 async，注入自定义实现时别忘了 ``await``。
    """

    model: str

    async def chat_json(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class LLMClient:
    """真实 LLM 客户端：异步 POST ``chat/completions``，失败按线性退避重试。

    Args:
        config: 可注入的配置对象；缺省使用全局 settings。
    """

    def __init__(self, config: Any = None) -> None:
        cfg = config or settings
        self.model = cfg.llm_model
        self.max_retries = max(0, int(cfg.llm_max_retries))
        # 统一补尾斜杠，httpx 的 base_url 才能正确拼接相对路径。
        base_url = cfg.llm_base_url.rstrip("/") + "/"
        headers = {"Content-Type": "application/json"}
        if cfg.llm_api_key:
            headers["Authorization"] = f"Bearer {cfg.llm_api_key}"

        verify: bool | str = True
        if cfg.llm_ca_cert and Path(cfg.llm_ca_cert).is_file():
            verify = cfg.llm_ca_cert

        # 生成类请求读超时通常远大于连接超时，分开配置避免误杀长响应。
        timeout = httpx.Timeout(
            connect=cfg.llm_connect_timeout,
            read=cfg.llm_read_timeout,
            write=cfg.llm_write_timeout,
            pool=30.0,
        )
        self.client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout, verify=verify)

    async def aclose(self) -> None:
        """关闭连接池；由 WorkflowService 在应用关闭时调用。"""
        await self.client.aclose()

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.7,
        model: str | None = None,
        stage: str | None = None,  # noqa: ARG002 - 仅 Mock 使用
        **hints: Any,  # noqa: ARG002 - 仅 Mock 使用
    ) -> str:
        """发起一次非流式对话，返回 ``choices[0].message.content`` 纯文本。

        重试覆盖网络错误与响应结构异常（HTTPError / KeyError 等）；
        最多 ``max_retries`` 次，间隔 1s、2s、3s…（异步 sleep）。全部失败后
        抛 RuntimeError，由 WorkflowService 统一把作品置为 failed。
        """
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
                response = await self.client.post("chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                return str(data["choices"][0]["message"]["content"] or "")
            except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败：{last_error}") from last_error

    async def chat_json(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> dict[str, Any]:
        """调用 chat 并强制解析为 JSON 对象。

        报错信息里带上 stage 与返回内容前 300 字，方便定位是提示词问题
        还是模型输出格式问题（五个阶段都依赖结构化结果）。
        """
        content = await self.chat(system_prompt, user_prompt, **kwargs)
        try:
            result = json.loads(strip_code_fence(content))
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型返回内容不是合法 JSON（{kwargs.get('stage') or 'unknown'}）：{content[:300]}") from exc
        if not isinstance(result, dict):
            raise ValueError(f"模型返回 JSON 不是对象：{type(result).__name__}")
        return result


class MockLLMClient:
    """离线演示用：按阶段返回满足校验的最小合法结构，不发起任何网络请求。

    返回结构必须与 prompts 里约定的 schema 一致，否则 ``stage_steps`` 的
    校验会失败——这是本地跑通全流程的关键，改结构时请同步看 stage_steps。
    """

    model = "mock-llm"

    async def aclose(self) -> None:
        """Mock 无连接池，保留空实现以满足协议。"""

    async def chat(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,
        *,
        temperature: float = 0.7,  # noqa: ARG002
        model: str | None = None,  # noqa: ARG002
        stage: str | None = None,
        **hints: Any,
    ) -> str:
        """按 chat_json 的结果序列化成 JSON 文本，模拟真实模型的文本返回。"""
        return json.dumps(
            await self.chat_json(system_prompt, user_prompt, stage=stage, **hints), ensure_ascii=False
        )

    async def chat_json(
        self,
        system_prompt: str,  # noqa: ARG002
        user_prompt: str,
        *,
        stage: str | None = None,
        **hints: Any,
    ) -> dict[str, Any]:
        """按 ``stage`` 分发四种阶段产物；hints 里的标题 / 原始描述用于填充内容。

        * ``planning`` -> creation_spec（含 bpm 与 ai_music_prompt）
        * ``lyrics``   -> song（分段歌词 dict）
        * ``polish``   -> 润色后的 song
        * ``music``    -> 音乐制作方案（style / 时长 / 目标模型）
        """
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
    """工厂：mock 模式或缺 API Key 时返回 MockLLMClient，否则返回真实客户端。

    这样 WorkflowService 无需感知运行模式，且离线环境也能完整演示。
    纯构造、无 IO，保持同步函数即可。
    """
    cfg = config or settings
    if cfg.mock_mode or not cfg.llm_api_key:
        return MockLLMClient()
    return LLMClient(cfg)
