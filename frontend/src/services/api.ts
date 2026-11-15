/**
 * 后端接口层：所有请求都走 services/index.ts 的 axios 实例，只对接真实后端。
 * 后端未启动时由页面状态栏提示「后端未连接」，不再有本地演示数据兜底。
 *
 * 接口版本：axios 的 baseURL 是 /v1（见 common/config.ts），apiUrls 只写
 * 不带版本号的相对路径；后端 constants.API_V1_PREFIX 是前缀的唯一来源。
 */
import { get, post } from "./index";
import type {
  GenerationCreate,
  GenerationResponse,
  HealthResponse,
  LLMTaskRequest,
  LLMTaskResponse,
} from "@/interface";
import { config } from "@/common/config";

/** 接口地址集中管理（唯一来源）；/v1 前缀由 config.API_BASE_URL 提供 */
export const apiUrls = {
  health: () => "/health",
  history: () => "/history",
  generations: () => "/generations",
  audio: (id: string) => `/generations/${id}/audio`,
  llm: () => "/llm",
};

/**
 * 把后端返回的 audio_url 解析成浏览器可直接请求的地址。
 *
 * 后端给的是根相对路径 ``/v1/generations/{id}/audio``，在两种部署下需要不同处理：
 * * ``API_BASE_URL=/v1``（Vite 代理 / 同源反代）：原样返回即可命中代理；
 * * ``API_BASE_URL=http://host:8788/v1``（直连后端）：用 ``new URL`` 补上域名，
 *   否则浏览器会去前端站点找 /v1/... 而拿到 404；
 * * 外链 http(s) / 协议相对地址：原样透传（blob:/data: 仅为兼容保留）。
 */
export function resolveAudioUrl(path: string): string {
  if (/^(?:https?:|blob:|data:|\/\/)/i.test(path)) return path;
  if (/^https?:\/\//i.test(config.API_BASE_URL)) {
    return new URL(path, config.API_BASE_URL).href;
  }
  return path;
}

/**
 * GET /v1/history —— 作品列表，前端每 POLL_INTERVAL 轮询。
 * silent: true 避免后端短暂掉线时错误提示刷屏；连接状态由页面状态栏展示。
 */
export function listHistory(): Promise<GenerationResponse[]> {
  return get<GenerationResponse[]>(apiUrls.history(), { silent: true });
}

/**
 * GET /v1/health —— 连接状态 + 今日生成配额。
 *
 * 配额字段（daily_limit / daily_remaining）由后端按自然日统计，前端只做展示：
 * 上限为 0 表示不限额，此时 daily_remaining 是 null，UI 不显示剩余量。
 * silent: true —— 探活失败不该弹全局错误（顶栏已有「后端未连接」状态）。
 */
export function fetchHealth(): Promise<HealthResponse> {
  return get<HealthResponse>(apiUrls.health(), { silent: true });
}

/** POST /v1/generations：创建作品，立即返回 queued，后台跑五阶段 */
export function createGeneration(payload: GenerationCreate): Promise<GenerationResponse> {
  return post<GenerationResponse>(apiUrls.generations(), payload);
}

/** POST /v1/llm：AI 扩写描述 / 写词 / 起标题等用户主动触发的辅助任务 */
export function runLLMTask(payload: LLMTaskRequest): Promise<LLMTaskResponse> {
  return post<LLMTaskResponse>(apiUrls.llm(), payload);
}
