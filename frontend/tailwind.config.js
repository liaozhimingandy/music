/**
 * 断点刻意与 TDesign 栅格（tdesign-vue-next 的 t-row / t-col）保持一致，
 * 避免「CSS 媒体查询」和「组件栅格」在同一个视口宽度上给出两套答案：
 *
 *   xs   <  768px   手机竖屏
 *   sm  >=  768px   手机横屏 / 小平板
 *   md  >=  992px   桌面（此处是三栏栅格与单栏切换的分界）
 *   lg  >= 1200px   宽屏桌面
 *   xl  >= 1400px   大屏
 *   xxl >= 1880px   超宽屏
 *
 * 覆盖了 Tailwind 默认的 sm(640) / lg(1024) / xl(1280) / 2xl(1536)，
 * 所以**任何 md: / lg: 前缀都表示 992px / 1200px**，不是 Tailwind 默认值。
 * 新增断点时先想清楚它对应 TDesign 的哪一档，别混用两套体系。
 */
const TDESIGN_SCREENS = {
  xs: "0px",
  sm: "768px",
  md: "992px",
  lg: "1200px",
  xl: "1400px",
  xxl: "1880px",
};

/** @type {import('tailwindcss').Config} */
export default {
  // 关闭 preflight，避免重置掉 TDesign 组件库自带的基础样式
  corePlugins: { preflight: false },
  content: ["./index.html", "./src/**/*.{ts,vue}"],
  theme: {
    // 注意是「替换」而不是 extend：extend 会把 Tailwind 默认的 2xl(1536px) 留下来，
    // 与 TDesign 的 xxl(1880px) 并存会造成两套体系混用。
    screens: TDESIGN_SCREENS,
    extend: {
      colors: {
        canvas: "#f5f6f8",
        surface: "#ffffff",
        muted: "#f7f8fa",
        line: "#e9ebf0",
        ink: "#1d2129",
        subtle: "#7a8090",
        brand: {
          50: "#fff5f5",
          100: "#ffe9e9",
          200: "#ffd0d0",
          500: "#ec4141",
          600: "#d62f2f",
          700: "#c20c0c",
        },
      },
      boxShadow: {
        panel: "0 6px 24px rgba(29, 33, 41, 0.06)",
        pop: "0 16px 48px rgba(29, 33, 41, 0.16)",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", '"PingFang SC"', '"Microsoft YaHei"', '"Segoe UI"', "sans-serif"],
      },
    },
  },
  plugins: [],
};
