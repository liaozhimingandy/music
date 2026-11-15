/**
 * 前端路由。
 *
 * 历史模式（createWebHistory）需要托管端做 SPA 回退：未知路径要返回带正确
 * 资源引用的 index.html（构建脚本已自动复制成 404.html）。这样 /studio、/foo
 * 这类前端路由由本文件处理，匹配不到的统一落到 NotFoundPage（返回 404 页面）。
 *
 * 若托管环境不方便配 SPA 回退，可改用 createWebHashHistory()：URL 带 #，
 * 不需要任何服务器配置，但链接形如 https://x/#/studio。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import StudioPage from "@/pages/StudioPage.vue";
import NotFoundPage from "@/pages/NotFoundPage.vue";

const routes: RouteRecordRaw[] = [
  { path: "/", name: "studio", component: StudioPage },
  { path: "/studio", redirect: "/" },
  // 兜底：任何未匹配的路由都渲染 404 页面。
  { path: "/:pathMatch(.*)*", name: "not-found", component: NotFoundPage },
];

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior() {
    return { top: 0 };
  },
});

export default router;
