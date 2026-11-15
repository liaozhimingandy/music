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
    plugins: [vue(), spa404()],
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
