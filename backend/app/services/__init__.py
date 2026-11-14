"""service 层：业务逻辑（编排、LLM、音乐生成、API 服务、接口防护）。

依赖方向：``routers -> services -> dal -> models``；service 不反向依赖 routers。
全链路异步：路由 / service / DAL 的 IO 方法均为 ``async def``，后台生成任务
由 ``runtime`` 用 asyncio.Task + Semaphore 调度，不再使用线程池。

模块速查（建议按此顺序阅读）：
* ``prompt_manager``  只读加载 / 渲染 prompts 模板（async）
* ``llm_client``      OpenAI 兼容聊天接口（httpx.AsyncClient）+ mock 实现
* ``music_provider``  ModelScope 音乐生成：提交、轮询、下载、去重、续跑
* ``stage_steps``     五个可独立复用的纯阶段函数（async）
* ``workflow_service`` 顺序编排五阶段并回写进度（async）
* ``song_service``    作品 CRUD、前端响应映射、幂等去重（async）
* ``llm_service``     /api/llm 三个轻量任务（async）
* ``rate_limiter``    进程内滑动窗口限流（async），供 deps.py 调用
* ``runtime``         单例装配点与生命周期（async），FastAPI Depends 从这里取服务
* ``cover_theme``     由 ID 推导封面配色（纯函数，同步）

横切模块：``app/ids.py`` 提供全局 UUIDv7 ID，``app/deps.py`` 是 FastAPI
依赖层（限流 / 去重客户端标识 / 请求体大小 / 路径参数校验）。
"""

from .cover_theme import compute_theme
from .llm_client import LLMClient, MockLLMClient, build_llm_client
from .llm_service import LLMService
from .music_provider import MockMusicProvider, MusicProvider, build_music_provider
from .prompt_manager import PromptManager
from .rate_limiter import RateLimitExceeded, SlidingWindowLimiter
from .song_service import CreateResult, SongService, TooManyActiveJobs, request_fingerprint
from .workflow_service import WorkflowService

__all__ = [
    "PromptManager",
    "LLMClient",
    "MockLLMClient",
    "build_llm_client",
    "MusicProvider",
    "MockMusicProvider",
    "build_music_provider",
    "WorkflowService",
    "SongService",
    "CreateResult",
    "TooManyActiveJobs",
    "request_fingerprint",
    "LLMService",
    "SlidingWindowLimiter",
    "RateLimitExceeded",
    "compute_theme",
]
