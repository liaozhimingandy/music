import { fileURLToPath, URL } from "node:url";
import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

const apiProxy = {
  "/api": {
    target: "http://127.0.0.1:8788",
    changeOrigin: true,
  },
};

/**
 * base 说明：
 * - 本地开发 / 自定义域名："/"
 * - GitHub Pages 项目页："/<仓库名>/"，由 CI 注入 VITE_BASE_PATH 覆盖；
 *   本地构建默认 "/"，保证 npm run preview 可直接查看（process.env 优先于 .env 文件）。
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const base = process.env.VITE_BASE_PATH || env.VITE_BASE_PATH || "/";

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
