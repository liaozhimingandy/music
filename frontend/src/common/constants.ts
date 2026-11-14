/**
 * 业务常量：提示词模板、生成阶段文案等。
 *
 * MAX_PROMPT_LENGTH 与后端的 500 字约束保持一致，修改时请同步
 * CreatePanel.vue 的 maxlength / clamp 与后端 llm_service 的兜底文案。
 * STAGE_LABELS 只用于左栏「生成中」占位卡片的本地进度展示，与后端五阶段
 * （backend/app/constants.py 的 STAGE_ORDER）不是同一套，不要混用。
 */

/** 歌曲描述（歌词描述）输入框最大字符数 */
export const MAX_PROMPT_LENGTH = 500;

/** 写词任务附加指令 */
export const LYRICS_INSTRUCTION = "请用中文创作歌词，使用 [主歌]、[副歌]、[桥段] 等段落标记，结构清晰、押韵自然。";

/** 用户未填写描述时的兜底创意 */
export const DEFAULT_SEED_PROMPT = "写一首关于夏日告别的华语流行歌";

/** 简单创作的阶段标签 */
export const STAGE_LABELS = {
  vocal: ["构思创意", "撰写歌词", "编曲生成", "混音输出"],
  instrumental: ["构思创意", "编曲生成", "混音输出"],
} as const;
