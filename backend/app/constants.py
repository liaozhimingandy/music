"""全局常量：阶段定义与前端约定的状态码。

维护提示：这里的值同时被 Python 后端与前端 TypeScript 使用（前端单独维护
一份在 src/common/constants.ts、src/common/utils.ts），改名必须两边同步。
尤其是 STATUS_* 四种取值，前端 statusText / statusTheme 只认识这四个。
"""

from __future__ import annotations

# 五阶段流程顺序（与 music.py 原型一致）
# 顺序即执行顺序，也是 song_stages 表预置记录的顺序；调整会改变进度展示。
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

# 每个阶段落盘的产物文件名；文件存在即代表该阶段已完成（断点续跑依据）。
# 文件名沿用原型，便于直接复用 outputs/ 下已有的历史产物。
STAGE_OUTPUTS: dict[str, str] = {
    STAGE_PLANNING: "stage1.json",
    STAGE_LYRICS: "stage2.json",
    STAGE_POLISH: "stage2_polished.json",
    STAGE_MUSIC: "stage3.json",
    STAGE_MUSIC_GENERATION: "music_result.json",
}

# 阶段中文名，用于 GET /api/generations/{id}/stages 的 label 字段。
STAGE_LABELS: dict[str, str] = {
    STAGE_PLANNING: "歌曲策划",
    STAGE_LYRICS: "歌词创作",
    STAGE_POLISH: "歌词润色",
    STAGE_MUSIC: "音乐制作方案",
    STAGE_MUSIC_GENERATION: "音频生成",
}

# 作品状态（前端 utils.ts 的 statusText / statusTheme 只认识这四种）
STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

# 阶段状态：与作品状态是两套独立枚举，不要混用。
STAGE_PENDING = "pending"
STAGE_RUNNING = "running"
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"

# 任务目录下的固定文件名
TASK_FILE = "task.json"
USER_INPUT_FILE = "user_input.txt"        # 用户原始描述，五个阶段共用
MUSIC_TASK_FILE = "music_task.json"       # 音乐生成任务状态（task_id / 指纹）
FINAL_AUDIO_NAME = "final_song"           # 最终音频主名，后缀由实际格式决定
