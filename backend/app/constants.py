"""全局常量：阶段定义与前端约定的状态码。"""

from __future__ import annotations

# 五阶段流程顺序（与 music.py 原型一致）
STAGE_PLANNING = "planning"
STAGE_LYRICS = "lyrics"
STAGE_POLISH = "polish"
STAGE_MUSIC = "music"
STAGE_MUSIC_GENERATION = "music_generation"

STAGE_ORDER: tuple[str, ...] = (
    STAGE_PLANNING,
    STAGE_LYRICS,
    STAGE_POLISH,
    STAGE_MUSIC,
    STAGE_MUSIC_GENERATION,
)

# 每个阶段落盘的产物文件名
STAGE_OUTPUTS: dict[str, str] = {
    STAGE_PLANNING: "stage1.json",
    STAGE_LYRICS: "stage2.json",
    STAGE_POLISH: "stage2_polished.json",
    STAGE_MUSIC: "stage3.json",
    STAGE_MUSIC_GENERATION: "music_result.json",
}

STAGE_LABELS: dict[str, str] = {
    STAGE_PLANNING: "歌曲策划",
    STAGE_LYRICS: "歌词创作",
    STAGE_POLISH: "歌词润色",
    STAGE_MUSIC: "音乐制作方案",
    STAGE_MUSIC_GENERATION: "音频生成",
}

# 前端 utils.ts 的 statusText / statusTheme 只认识这四种
STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

STAGE_PENDING = "pending"
STAGE_RUNNING = "running"
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"

TASK_FILE = "task.json"
USER_INPUT_FILE = "user_input.txt"
MUSIC_TASK_FILE = "music_task.json"
FINAL_AUDIO_NAME = "final_song"
