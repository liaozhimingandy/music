"""统一后端启动入口。

之前 uvicorn 在 Dockerfile 的 CMD（以及启动脚本）里写死了
``--host 0.0.0.0 --port 8788``，导致在 .env 里改 HOST / PORT 完全没反应——
settings.host / settings.port 只被解析却从没真正拿去绑定。

现在统一由这里读取 ``app.config.settings`` 的 host / port 再启动 uvicorn，
.env 里的绑定地址才真正生效。

变量名优先级（见 config.py 的 AliasChoices）：
    HOST → MUSIC_STUDIO_HOST → ACE_STEP_HOST
    PORT → MUSIC_STUDIO_PORT → ACE_STEP_PORT
即直接写 ``HOST=`` / ``PORT=`` 即可，旧的 ``ACE_STEP_*`` 别名继续兼容。
"""
from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        # 对应原 CMD 的 --no-server-header：少暴露服务器指纹。
        server_header=False,
    )


if __name__ == "__main__":
    main()
