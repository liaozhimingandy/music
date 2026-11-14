/**
 * 业务常量：与后端约定一致的输入约束。
 *
 * MAX_PROMPT_LENGTH 与后端 schemas.py 的 MAX_PROMPT_LENGTH（500）保持一致，
 * 修改时请同步 CreatePanel.vue 的 maxlength / clamp 与后端校验。
 * 生成进度不再由前端模拟：占位卡片只表示「提交中」，真实阶段状态来自
 * 后端 current_stage（见 backend/app/constants.py 的 STAGE_ORDER）。
 */

/** 歌曲描述（歌词描述）输入框最大字符数 */
export const MAX_PROMPT_LENGTH = 500;
