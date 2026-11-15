/**
 * Axios 实例与全局拦截器（统一请求出口）
 *
 * 职责：
 * 1. 创建统一实例：baseURL / 超时 / JSON 头，地址来自 common/config.ts
 * 2. 请求拦截：统一注入鉴权头等通用信息
 * 3. 响应拦截：直接返回 response.data；错误按状态码归一化并可选全局提示
 * 4. 暴露 get / post / del 三个类型化方法供 services/api.ts 使用
 *
 * 注意：作品列表等高频轮询请求统一传 silent: true，避免后端短暂掉线时
 * 错误提示刷屏（连接状态由页面状态栏展示）；其余请求正常弹出全局提示。
 */
import axios from "axios";
import type { AxiosRequestConfig } from "axios";
import { MessagePlugin } from "tdesign-vue-next";

import { config } from "@/common/config";

/** 扩展请求配置：silent=true 时跳过全局错误提示 */
export interface RequestOptions extends AxiosRequestConfig {
  silent?: boolean;
}

const instance = axios.create({
  baseURL: config.API_BASE_URL,
  timeout: config.REQUEST_TIMEOUT,
  headers: {
    "Content-Type": "application/json;charset=UTF-8",
    Accept: "application/json",
  },
});

// ------------------------- 请求拦截 -------------------------
instance.interceptors.request.use(
  (requestConfig) => {
    // 预留鉴权：后端启用登录后，从 localStorage 取 token 统一注入
    const token = localStorage.getItem("access_token");
    if (token) requestConfig.headers.Authorization = `Bearer ${token}`;
    return requestConfig;
  },
  (error) => Promise.reject(error),
);

// ------------------------- 响应拦截 -------------------------
instance.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const options = (error?.config || {}) as RequestOptions;
    if (!options.silent && axios.isAxiosError(error)) {
      const status = error.response?.status;
      if (!error.response) {
        void MessagePlugin.error(error.code === "ECONNABORTED" ? "请求超时，请稍后重试" : "网络连接失败，请检查网络");
      } else if (status === 404) {
        void MessagePlugin.warning("接口不存在");
      } else if (status && status >= 500) {
        void MessagePlugin.error("服务器暂时不可用");
      } else if (status === 401 || status === 403) {
        void MessagePlugin.error("登录状态已失效");
      }
    }
    return Promise.reject(error);
  },
);

/** 判断是否为「后端不可达」：网络错误 / 超时 / 网关错误 / 接口不存在 */
export function isConnectionError(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false;
  if (!error.response) return true;
  const status = error.response.status;
  return status >= 500 || status === 404;
}

/**
 * 响应拦截器已把 response.data 直接返回，但 axios 1.21 起方法返回的是条件类型
 * AxiosResponseResult<T, R, D, P>，泛型 T 未确定时 TS 无法化简。
 * 因此在封装边界处统一做一次断言，让调用方拿到业务数据类型。
 */
function unwrap<T>(promise: Promise<unknown>): Promise<T> {
  return promise as Promise<T>;
}

/** 三个类型化快捷方法；options.silent 控制是否弹全局错误提示 */
export function get<T>(url: string, options: RequestOptions = {}): Promise<T> {
  return unwrap<T>(instance.get(url, options));
}

export function post<T>(url: string, data?: unknown, options: RequestOptions = {}): Promise<T> {
  return unwrap<T>(instance.post(url, data, options));
}

export function del<T>(url: string, options: RequestOptions = {}): Promise<T> {
  return unwrap<T>(instance.delete(url, options));
}

export default instance;
