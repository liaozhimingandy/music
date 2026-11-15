import { fileURLToPath, URL } from "node:url";
import { defineConfig, loadEnv, type Plugin } from "vite";
import vue from "@vitejs/plugin-vue";
import fs from "node:fs";
import path from "node:path";

/**
 * base 说明：
 * - 本地开发 / 自定义域名："/"
 * - GitHub Pages 项目页："/<仓库名>/"，由 CI 注入 VITE_BASE_PATH 覆盖；
 *   本地构建默认 "/"，保证 npm run preview 可直接查看（process.env 优先于 .env 文件）。
 */
/**
 * 构建后把 index.html 复制成 404.html，供 GitHub Pages / Cloudflare 的 SPA 回退使用：
 * 未知路由（如 /foo/bar）会返回这个带正确资源引用的壳，再由前端路由渲染 NotFound 组件。
 * 注意必须复制「构建后的」index.html（已注入带 hash 的资源路径），不能复制源码里的 index.html。
 */
function spa404(): Plugin {
  let outDir = "dist";
  return {
    name: "spa-404",
    apply: "build",
    configResolved(config) {
      outDir = config.build.outDir;
    },
    closeBundle() {
      const indexFile = path.resolve(outDir, "index.html");
      const notFoundFile = path.resolve(outDir, "404.html");
      if (fs.existsSync(indexFile)) {
        fs.copyFileSync(indexFile, notFoundFile);
        this.info?.("已生成 404.html（index.html 副本，用于 SPA 回退）");
      }
    },
  };
}

/**
 * 不再配置 /v1 同源代理：前端直连后端（VITE_APP_API_BASE_URL 为绝对地址，
 * 见 frontend/.env.development），跨域由后端 CORS_ORIGINS 显式放行。
 * 这样开发环境的请求链路与生产一致，不会出现「代理指向错端口」这类隐蔽故障。
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
    plugins: [vue(), spa404()],
    server: {
      port: 5175,
      host: "0.0.0.0",
    },
    preview: {
      port: 5175,
      host: "127.0.0.1",
    },
  };
});
