/**
 * 前后端数据契约（与 backend/app/models/schemas.py 一一对应）。
 *
 * 维护约定：
 * - GenerationResponse 字段改名必须同步后端 GenerationResponse 与
 *   SongService.to_response()，否则作品库会渲染出 undefined。
 * - status 只用 queued / processing / ready / failed，文案映射在 common/utils.ts。
 * - key 对应数据库的 musical_key；audio_url 是 /v1/... 相对路径或外链 URL，
 *   为空表示音频尚未生成，播放器会禁用播放按钮。
 * - metadata 是不定型扩展字段，后端流程会把音乐方案摘要等暂存在这里。
 */

export type TaskType = "text2music";
/**
 * 模型标识不在这里枚举：唯一来源是后端 `MUSIC_MODEL` 配置
 * （backend/app/config.py，默认 ACE-Step/acestep-v15-xl-sft），
 * 可通过 GET /v1/health 的 music_model 字段读取。
 * 提交时前端不传 model_variant，由 SongService 用配置回填；
 * 响应里还可能是历史记录的 base / turbo / shift，所以放宽成 string。
 */
export type ModelVariant = string;

/** 左栏「简单创作」表单的值 */
export type CreateFormValues = {
  prompt: string;
  instrumental: boolean;
};

/** POST /v1/generations 请求体（只提交用户原始输入；标题/歌词/模型都由后端决定） */
export type GenerationCreate = {
  title?: string;
  task_type: TaskType;
  mode: "simple";
  model_variant?: string;
  inputs: {
    prompt: string;
    lyrics?: string;
    instrumental: boolean;
  };
};

/** GET /v1/history、POST /v1/generations 返回的作品 */
export type GenerationResponse = {
  id: string;
  title?: string | null;
  task_type: TaskType;
  mode: string;
  model_variant: ModelVariant;
  status: string;
  prompt?: string | null;
  lyrics?: string | null;
  instrumental: boolean;
  duration_seconds?: number | null;
  bpm?: number | null;
  key?: string | null;
  time_signature?: string | null;
  audio_url?: string | null;
  cover_color?: string | null;
  cover_icon?: string | null;
  cover_image_url?: string | null;
  error_message?: string | null;
  metadata?: Record<string, unknown>;
  /** 生成中时后端正在执行的阶段（planning / lyrics / polish / music_prompt / music_generation） */
  current_stage?: string | null;
  created_at: string;
  updated_at: string;
};

/** POST /v1/llm 请求与响应 */
export type LLMTaskRequest = {
  task: "prompt" | "lyrics" | "title";
  seed_prompt: string;
  instrumental: boolean;
  style_tags: string[];
};

export type LLMTaskResponse = {
  task: string;
  output: string;
  provider: string;
  metadata?: Record<string, string>;
};

/** 作品库里的「生成中」占位卡片 */
export type PendingGeneration = {
  tempId: string;
  title: string;
  prompt: string;
  stageLabels: string[];
  stageIndex: number;
  error?: string;
};

/**
 * GET /v1/health 响应（对应后端 models/schemas.py 的 HealthResponse）。
 *
 * daily_limit = 0 表示不限额，此时 daily_remaining 为 null；
 * 前端只在 remaining 是数字时显示「今日还剩 X 首」，不限额时不打扰用户。
 */
export type HealthResponse = {
  status: string;
  app: string;
  mock_mode: boolean;
  database: string;
  music_model?: string | null;
  daily_limit?: number | null;
  daily_used?: number | null;
  daily_remaining?: number | null;
  daily_reset_at?: string | null;
};
