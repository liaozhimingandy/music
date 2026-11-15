"""全局常量：阶段定义与前端约定的状态码。

维护提示：这里的值同时被 Python 后端与前端 TypeScript 使用（前端单独维护
一份在 src/common/constants.ts、src/common/utils.ts），改名必须两边同步。
尤其是 STATUS_* 四种取值，前端 statusText / statusTheme 只认识这四个。
"""

from __future__ import annotations

# ---- HTTP API 版本前缀（唯一来源）----
# 所有业务接口（含健康检查）都挂在 routers/__init__.py 的 api_v1 聚合路由下，
# 最终路径 = API_V1_PREFIX + 子路由相对路径，例如 /v1/history。
# 升级 v2 时新增一个 APIRouter(prefix="/v2") 即可，不要改各 router 的 prefix。
API_V1_PREFIX = "/v1"

# 本地音频的对外相对地址；SongService.to_response 生成 audio_url 时用，
# 前端再由 services/api.ts 的 resolveAudioUrl() 解析成可请求地址。
AUDIO_URL_TEMPLATE = f"{API_V1_PREFIX}/generations/{{song_id}}/audio"

# 五阶段流程顺序（与 music.py 原型一致）
# 顺序即执行顺序，也是 song_stages 表预置记录的顺序；调整会改变进度展示。
# 注意：第四阶段叫 STAGE_MUSIC_PROMPT（生成「音乐制作方案」提示词），
# 与第五阶段的 STAGE_MUSIC_GENERATION（真正调音乐生成接口）区分开。
STAGE_PLANNING = "planning"
STAGE_LYRICS = "lyrics"
STAGE_POLISH = "polish"
STAGE_MUSIC_PROMPT = "music_prompt"
STAGE_MUSIC_GENERATION = "music_generation"

STAGE_ORDER: tuple[str, ...] = (
    STAGE_PLANNING,
    STAGE_LYRICS,
    STAGE_POLISH,
    STAGE_MUSIC_PROMPT,
    STAGE_MUSIC_GENERATION,
)

# 阶段名 -> prompts/ 子目录名。绝大多数阶段与目录同名，唯独
# STAGE_MUSIC_PROMPT 沿用 prompts/music/ 目录（提示词文件不能移动/改名），
# PromptManager.get_stage 据此解析模板路径。
STAGE_PROMPT_DIRS: dict[str, str] = {
    STAGE_MUSIC_PROMPT: "music",
}

# 每个阶段落盘的产物文件名；文件存在即代表该阶段已完成（断点续跑依据）。
# 文件名沿用原型，便于直接复用 outputs/ 下已有的历史产物。
STAGE_OUTPUTS: dict[str, str] = {
    STAGE_PLANNING: "stage1.json",
    STAGE_LYRICS: "stage2.json",
    STAGE_POLISH: "stage2_polished.json",
    STAGE_MUSIC_PROMPT: "stage3.json",
    STAGE_MUSIC_GENERATION: "music_result.json",
}

# 阶段中文名，用于 GET /v1/generations/{id}/stages 的 label 字段。
STAGE_LABELS: dict[str, str] = {
    STAGE_PLANNING: "歌曲策划",
    STAGE_LYRICS: "歌词创作",
    STAGE_POLISH: "歌词润色",
    STAGE_MUSIC_PROMPT: "音乐制作方案",
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
