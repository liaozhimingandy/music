"""POST /api/llm：前端「AI 扩写描述 / 写词 / 起标题」。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import LLMTaskRequest, LLMTaskResponse
from ..services.runtime import get_llm_service

router = APIRouter(prefix="/api/llm", tags=["llm"])


@router.post("", response_model=LLMTaskResponse)
def run_llm_task(payload: LLMTaskRequest) -> LLMTaskResponse:
    """执行一个轻量 LLM 任务；上游异常统一转成 502，便于前端展示文案。

    mock 模式或无 Key 时 LLMService 会走本地兜底，不会走到这个异常分支。
    """
    try:
        return get_llm_service().run(payload)
    except Exception as exc:  # noqa: BLE001 - 统一转成可展示的 502
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc
