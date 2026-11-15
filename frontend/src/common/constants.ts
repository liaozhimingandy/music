/**
 * 业务常量：与后端约定一致的输入约束与流程枚举。
 *
 * MAX_PROMPT_LENGTH 与后端 schemas.py 的 MAX_PROMPT_LENGTH（500）保持一致，
 * 修改时请同步 CreatePanel.vue 的 maxlength / clamp 与后端校验。
 * STAGE_ORDER / STAGE_LABELS 是 backend/app/constants.py 里同名常量的镜像，
 * 用于在「生成中」卡片上把 current_stage 翻译成中文阶段名并算进度百分比；
 * 后端调整阶段顺序或改名时，这里必须同步（否则进度会停在 0%）。
 */

/** 歌曲描述（歌词描述）输入框最大字符数 */
export const MAX_PROMPT_LENGTH = 500;

/** 五阶段执行顺序（与后端 STAGE_ORDER 一致） */
export const STAGE_ORDER = ["planning", "lyrics", "polish", "music_prompt", "music_generation"] as const;

/** 阶段中文名（与后端 STAGE_LABELS 一致） */
export const STAGE_LABELS: Record<string, string> = {
  planning: "歌曲策划",
  lyrics: "歌词创作",
  polish: "歌词润色",
  music_prompt: "音乐制作方案",
  music_generation: "音频生成",
};

/** current_stage -> 中文名；未知阶段原样返回，不留空白 */
export function stageLabel(stage?: string | null): string {
  if (!stage) return "准备中";
  return STAGE_LABELS[stage] || stage;
}

/**
 * 已完成的阶段数（0 ~ 5）。current_stage 表示「正在跑」的阶段，
 * 所以进度取它的下标（前面的阶段视为已完成）；全部跑完由 status 兜底。
 */
export function completedStageCount(stage?: string | null, status?: string): number {
  if (status === "ready" || status === "completed") return STAGE_ORDER.length;
  if (!stage) return 0;
  const index = STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number]);
  return index < 0 ? 0 : index;
}
