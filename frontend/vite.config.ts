import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  envDir: "..",
  optimizeDeps: {
    include: ["mammoth", "docx"],
  },
  server: {
    host: true,
    port: 5173,
    allowedHosts: true,
    // Do not pin clientPort. Vite then uses the page's own port:
    // PC  http://127.0.0.1:5173  → ws://127.0.0.1:5173
    // Phone http://192.168.137.1:8080 → ws://192.168.137.1:8080 (portproxy)
    // Pinning 8080 made the PC :5173 page throw "[vite] failed to connect to websocket".
    proxy: {
      "/api/sessions": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
        ws: true,
        timeout: 0,
        proxyTimeout: 0,
      },
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
});
