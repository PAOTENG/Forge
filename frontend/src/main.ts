/**
 * 值班台入口。先挂 Boot.vue（健康检查/登录），通过后再进 App.vue 工作台。
 * Agent 不在这里跑，只负责把页面接到 :8787。
 */
import { createApp } from "vue";
import { createPinia } from "pinia";
import Boot from "./Boot.vue";
import "./style.css";

createApp(Boot).use(createPinia()).mount("#app");
