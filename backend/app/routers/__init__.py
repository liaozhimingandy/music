"""FastAPI 路由层：只做参数校验与响应组装，业务逻辑一律下沉到 service。

四个模块：
* ``meta``         /health
* ``history``      /api/history
* ``generations``  /api/generations/*（创建 / 详情 / 阶段 / 音频 / 删除）
* ``llm``          /api/llm

新增接口时：在 ``models/schemas.py`` 定义请求响应模型 -> 在对应 router 加
端点 -> 在 ``main.py`` 注册（已注册的 router 内新增无需改动 main）。
"""

from . import generations, history, llm, meta

__all__ = ["generations", "history", "llm", "meta"]
