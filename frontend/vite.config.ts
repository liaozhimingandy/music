import { fileURLToPath, URL } from "node:url";
import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

/**
 * base 说明：
 * - 本地开发 / 自定义域名："/"
 * - GitHub Pages 项目页："/<仓库名>/"，由 CI 注入 VITE_BASE_PATH 覆盖；
 *   本地构建默认 "/"，保证 npm run preview 可直接查看（process.env 优先于 .env 文件）。
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const base = process.env.VITE_BASE_PATH || env.VITE_BASE_PATH || "/";

  // 与后端 API_V1_PREFIX（/v1）保持一致；dev 与 preview 共用同一份代理规则。
  // 后端端口可用 VITE_API_PROXY_TARGET 覆盖（例如自己用 8000 端口启动后端时）。
  // 走同源代理（VITE_APP_API_BASE_URL=/v1）时完全不产生跨域请求，是最省心的方案；
  // 直连完整地址时才依赖后端 CORS。
  const proxyTarget =
    process.env.VITE_API_PROXY_TARGET || env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8788";
  const apiProxy = { "/v1": { target: proxyTarget, changeOrigin: true } };

  return {
    base,
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    plugins: [vue()],
    server: {
      port: 5175,
      host: "0.0.0.0",
      proxy: apiProxy,
    },
    preview: {
      port: 5175,
      host: "127.0.0.1",
      proxy: apiProxy,
    },
  };
});
