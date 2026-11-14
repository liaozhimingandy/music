/** @type {import('tailwindcss').Config} */
export default {
  // 关闭 preflight，避免重置掉 TDesign 组件库自带的基础样式
  corePlugins: { preflight: false },
  content: ["./index.html", "./src/**/*.{ts,vue}"],
  theme: {
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
