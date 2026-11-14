"""service 层：业务逻辑（编排、LLM、音乐生成、API 服务）。

依赖方向：service -> dal -> model；service 不反向依赖 routers。
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
