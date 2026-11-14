"""FastAPI 路由层：只做参数校验与响应组装，业务逻辑一律下沉到 service。

版本前缀
--------
本模块导出 ``api_v1``：一个带 ``constants.API_V1_PREFIX``（当前 ``/v1``）
前缀的聚合路由，把四个子 router 全部挂在下面。``main.py`` 只需注册一次，
以后新增 v1 接口在对应 router 里加端点即可；升级到 v2 时在这里新建
 ``APIRouter(prefix="/v2")``，旧客户端不受影响。

子路由一律用**不带版本号的相对路径**（``/history`` / ``/generations`` …），
保证前缀只有一处定义：

* ``meta``         /v1/health
* ``history``      /v1/history
* ``generations``  /v1/generations/*（创建 / 详情 / 阶段 / 音频 / 删除）
* ``llm``          /v1/llm

统一约定：
* 所有端点都是 ``async def``，服务用 ``Depends(get_song_service)`` /
  ``Depends(get_llm_service)`` 注入（见 deps.py 与 services/runtime.py）；
* 防护依赖挂在 router 或端点上，路由函数体不写限流代码；
* 路径参数用 ``deps.SongId`` 统一校验 32 位 hex。

新增接口时：在 ``models/schemas.py`` 定义请求响应模型 -> 在对应 router 加
端点 -> 在 ``docs/api.md`` 登记路径（main.py 无需改动）。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..constants import API_V1_PREFIX
from . import generations, history, llm, meta

# 唯一的 v1 聚合路由；注册顺序不影响匹配。
api_v1 = APIRouter(prefix=API_V1_PREFIX)
api_v1.include_router(meta.router)
api_v1.include_router(history.router)
api_v1.include_router(generations.router)
api_v1.include_router(llm.router)

__all__ = ["api_v1", "generations", "history", "llm", "meta"]
