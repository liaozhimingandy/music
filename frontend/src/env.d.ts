/// <reference types="vite/client" />

/** Vite 环境变量类型声明；新增 VITE_* 变量时在此登记，避免 TS 推成 any。 */
interface ImportMetaEnv {
  readonly VITE_APP_API_BASE_URL?: string;
  readonly VITE_APP_IS_DEBUG?: string;
  readonly VITE_BASE_PATH?: string;
  readonly VITE_APP_BUILD?: string;
}
