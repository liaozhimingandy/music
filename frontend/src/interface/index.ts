/** 与后端 backend/app/schemas 对齐的数据结构 */

export type TaskType = "text2music" | "cover" | "repaint";
export type ModelVariant = "base" | "turbo" | "shift";

/** 左栏「简单创作」表单的值 */
export type CreateFormValues = {
  prompt: string;
  instrumental: boolean;
};

/** POST /api/generations 请求体（前端只提交简单模式所需字段） */
export type GenerationCreate = {
  title: string;
  task_type: TaskType;
  mode: "simple";
  model_variant: ModelVariant;
  inputs: {
    prompt: string;
    lyrics: string;
    instrumental: boolean;
  };
};

/** GET /api/history、POST /api/generations 返回的作品 */
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
  created_at: string;
  updated_at: string;
};

/** POST /api/llm 请求与响应 */
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
