"""service 层：业务逻辑（编排、LLM、音乐生成、API 服务）。

依赖方向：``routers -> services -> dal -> models``；service 不反向依赖 routers。

模块速查（建议按此顺序阅读）：
* ``prompt_manager``  只读加载 / 渲染 prompts 模板
* ``llm_client``      OpenAI 兼容聊天接口 + mock 实现
* ``music_provider``  ModelScope 音乐生成：提交、轮询、下载、去重、续跑
* ``stage_steps``     五个可独立复用的纯阶段函数
* ``workflow_service`` 顺序编排五阶段并回写进度
* ``song_service``    作品 CRUD 与前端响应映射
* ``llm_service``     /api/llm 三个轻量任务
* ``runtime``         单例 / 线程池装配点与生命周期
* ``cover_theme``     由 ID 推导封面配色
"""

from .cover_theme import compute_theme
from .llm_client import LLMClient, MockLLMClient, build_llm_client
from .llm_service import LLMService
from .music_provider import MockMusicProvider, MusicProvider, build_music_provider
from .prompt_manager import PromptManager
from .song_service import SongService
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
    "LLMService",
    "compute_theme",
]
