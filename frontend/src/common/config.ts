/**
 * 运行期配置：统一读取 Vite 环境变量，避免在业务代码里散落 import.meta.env。
 * 新增环境变量时，同时在 src/env.d.ts 声明类型、在 .env.* 提供默认值。
 */
export const config = {
  /** 后端 v1 接口前缀：开发环境走 Vite 的 /v1 代理，生产可配置为完整域名（含 /v1） */
  API_BASE_URL: import.meta.env.VITE_APP_API_BASE_URL || "/v1",
  /** 是否输出调试日志 */
  IS_DEBUG: import.meta.env.VITE_APP_IS_DEBUG === "True",
  /** 作品列表轮询间隔（毫秒） */
  POLL_INTERVAL: 2000,
  /** 普通请求超时（毫秒） */
  REQUEST_TIMEOUT: 8000,
  /** 后端在线探测超时（毫秒），静态托管时应尽快失败 */
  PROBE_TIMEOUT: 1200,
} as const;
