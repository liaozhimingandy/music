import { onMounted, onUnmounted, ref } from "vue";

/**
 * 把 CSS 媒体查询的结果暴露成 Vue 的 ref。
 *
 * 为什么需要它：StudioPage 要在「桌面三栏栅格」和「窄屏单栏 + 顶部切换」之间
 * 切换的是**组件树**（两套不同的 DOM），而不只是样式。CSS 媒体查询做不到
 * 「不渲染另一套 DOM」，所以必须先知道当前断点。
 *
 * 为什么不用 VueUse：项目没有引入额外依赖，这里十几行就够了。
 *
 * 断点取值与 TDesign 栅格一致（见 tailwind.config.js 的 TDESIGN_SCREENS）：
 * md=992px 是「桌面 / 窄屏」的分界。
 *
 * 初始值是**同步**取到的（不是 onMounted 里才赋值），否则首帧会先按窄屏渲染、
 * 挂载后再跳回桌面布局，出现明显的闪动。本项目是纯客户端 SPA，没有 SSR 顾虑。
 */
export function useMediaQuery(query: string) {
  const mql = typeof window !== "undefined" ? window.matchMedia(query) : null;
  const matches = ref(Boolean(mql?.matches));

  function update() {
    matches.value = Boolean(mql?.matches);
  }

  onMounted(() => {
    // 挂载时再校准一次：构造阶段的读值可能早于首屏布局完成
    update();
    mql?.addEventListener("change", update);
  });

  onUnmounted(() => {
    mql?.removeEventListener("change", update);
  });

  return matches;
}
