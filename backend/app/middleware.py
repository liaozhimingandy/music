"""接口层中间件：把未捕获异常转成带 CORS 头的 JSON 500 响应。

排查背景
--------
浏览器报 ``No 'Access-Control-Allow-Origin' header`` 时不一定是 CORS 配错：
Starlette 的 ``ServerErrorMiddleware`` 永远在最外层，后端抛出未捕获异常时由它
直接生成纯文本 500，这个响应**不会经过** CORSMiddleware，于是没有 CORS 头。
前端只看到「跨域失败」，真正的后端异常被掩盖，极难定位（本项目就曾因
Pydantic 响应校验失败触发过一次）。

做法：在 CORSMiddleware **内层**放这个纯 ASGI 中间件，把异常转成
``JSONResponse``，让响应继续走外层的 CORS 流程；同时在服务端日志里留下
完整 traceback。注册顺序见 ``main.py``：先 add 本中间件，再 add CORSMiddleware。

注意：若响应头已经发出（streaming 中途出错），无法再替换响应，原样向上抛，
交给 ServerErrorMiddleware 记录。这里不用 BaseHTTPMiddleware，避免影响
FileResponse 的流式音频响应。
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class UnhandledErrorMiddleware:
    """兜底中间件；只处理 HTTP 请求，非 HTTP（如 lifespan）直接透传。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_with_flag(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_with_flag)
        except Exception:  # noqa: BLE001 - 兜底就要接住所有异常
            if response_started:
                raise
            logger.exception("未处理的异常：%s %s", scope.get("method"), scope.get("path"))
            response = JSONResponse(
                status_code=500,
                content={"detail": "服务器内部错误，请查看后端日志"},
            )
            await response(scope, receive, send_with_flag)
