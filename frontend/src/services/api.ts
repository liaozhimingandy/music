/**
 * 后端接口层：所有请求都走 services/index.ts 的 axios 实例。
 *
 * 启动时探测 GET /api/history：拿到数组说明后端在线，走真实接口；
 * 否则进入演示模式，用 services/demo.ts 的本地数据兜底，
 * 保证本地预览与 GitHub Pages 静态托管时界面和生成流程仍可用。
 * 后端启动后刷新页面即自动切回真实接口。
 */
import * as demo from "./demo";
import { del, get, isConnectionError, post } from "./index";
import type { GenerationCreate, GenerationResponse, LLMTaskRequest, LLMTaskResponse } from "@/interface";
import { config } from "@/common/config";

/** 接口地址集中管理（唯一来源） */
export const apiUrls = {
  history: () => "/history",
  generations: () => "/generations",
  generation: (id: string) => `/generations/${id}`,
  llm: () => "/llm",
};

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
        // GitHub Pages 会把 /api/* 回退成 HTML，用 Array.isArray 判定可避免误判为在线
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

/** GET /api/history */
export function listHistory(): Promise<GenerationResponse[]> {
  return call(
    () => get<GenerationResponse[]>(apiUrls.history(), { silent: true }),
    () => demo.demoHistory(),
  );
}

/** POST /api/generations */
export function createGeneration(payload: GenerationCreate): Promise<GenerationResponse> {
  return call(
    () => post<GenerationResponse>(apiUrls.generations(), payload, { silent: true }),
    () => demo.demoCreate(payload),
  );
}

/** DELETE /api/generations/{id} */
export function deleteGeneration(id: string): Promise<void> {
  return call(
    async () => {
      await del<unknown>(apiUrls.generation(id), { silent: true });
    },
    () => demo.demoDelete(id),
  );
}

/** POST /api/llm */
export function runLLMTask(payload: LLMTaskRequest): Promise<LLMTaskResponse> {
  return call(
    () => post<LLMTaskResponse>(apiUrls.llm(), payload, { silent: true }),
    () => demo.demoLLM(payload.task),
  );
}
