/**
 * 后端接口层：所有请求都走 services/index.ts 的 axios 实例。
 *
 * 启动时探测 GET /v1/history：拿到数组说明后端在线，走真实接口；
 * 否则进入演示模式，用 services/demo.ts 的本地数据兜底，
 * 保证本地预览与 GitHub Pages 静态托管时界面和生成流程仍可用。
 * 后端启动后刷新页面即自动切回真实接口。
 *
 * 接口版本：axios 的 baseURL 是 /v1（见 common/config.ts），apiUrls 只写
 * 不带版本号的相对路径；后端 constants.API_V1_PREFIX 是前缀的唯一来源。
 */
import * as demo from "./demo";
import { del, get, isConnectionError, post } from "./index";
import type { GenerationCreate, GenerationResponse, LLMTaskRequest, LLMTaskResponse } from "@/interface";
import { config } from "@/common/config";

/** 接口地址集中管理（唯一来源）；/v1 前缀由 config.API_BASE_URL 提供 */
export const apiUrls = {
  history: () => "/history",
  generations: () => "/generations",
  generation: (id: string) => `/generations/${id}`,
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
 * * 演示数据的 blob: / data: / 外链 http(s) 地址：原样透传。
 */
export function resolveAudioUrl(path: string): string {
  if (/^(?:https?:|blob:|data:|\/\/)/i.test(path)) return path;
  if (/^https?:\/\//i.test(config.API_BASE_URL)) {
    return new URL(path, config.API_BASE_URL).href;
  }
  return path;
}

/** 探测结果三态：unknown 未探测 / online 真实接口 / offline 演示模式 */
type BackendState = "unknown" | "online" | "offline";
let backendState: BackendState = "unknown";
let probePromise: Promise<boolean> | null = null;

/** 后端在线探测：只做一次，结果缓存 */
async function probe(): Promise<boolean> {
  if (backendState !== "unknown") return backendState === "online";
  if (!probePromise) {
    probePromise = (async () => {
      try {
        const data = await get<unknown>(apiUrls.history(), { timeout: config.PROBE_TIMEOUT, silent: true });
        // GitHub Pages 会把 /v1/* 回退成 HTML，用 Array.isArray 判定可避免误判为在线
        backendState = Array.isArray(data) ? "online" : "offline";
      } catch {
        backendState = "offline";
      }
      return backendState === "online";
    })().finally(() => {
      probePromise = null;
    });
  }
  return probePromise;
}

/**
 * 统一调用入口：后端在线走真实请求；请求过程中后端掉线则转成演示模式。
 * 业务错误（4xx 等）原样抛出，只有连接类错误才降级。
 */
async function call<T>(request: () => Promise<T>, fallback: () => T | Promise<T>): Promise<T> {
  if (!(await probe())) return fallback();
  try {
    return await request();
  } catch (error) {
    if (isConnectionError(error)) {
      backendState = "offline";
      return fallback();
    }
    throw error;
  }
}

/** 返回 true 表示处于演示模式（后端未连接） */
export async function detectBackend(): Promise<boolean> {
  return !(await probe());
}

/** GET /v1/history */
export function listHistory(): Promise<GenerationResponse[]> {
  return call(
    () => get<GenerationResponse[]>(apiUrls.history(), { silent: true }),
    () => demo.demoHistory(),
  );
}

/** POST /v1/generations */
export function createGeneration(payload: GenerationCreate): Promise<GenerationResponse> {
  return call(
    () => post<GenerationResponse>(apiUrls.generations(), payload, { silent: true }),
    () => demo.demoCreate(payload),
  );
}

/** DELETE /v1/generations/{id} */
export function deleteGeneration(id: string): Promise<void> {
  return call(
    async () => {
      await del<unknown>(apiUrls.generation(id), { silent: true });
    },
    () => demo.demoDelete(id),
  );
}

/** POST /v1/llm */
export function runLLMTask(payload: LLMTaskRequest): Promise<LLMTaskResponse> {
  return call(
    () => post<LLMTaskResponse>(apiUrls.llm(), payload, { silent: true }),
    () => demo.demoLLM(payload.task),
  );
}
