/** 应用入口：引入 TDesign 组件样式与全局样式，挂载到 index.html 的 #app。 */
import { createApp } from "vue";

import "tdesign-vue-next/es/style/index.css";
import "./style.css";
import App from "./App.vue";

createApp(App).mount("#app");
