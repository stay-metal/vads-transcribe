import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// SPA билдится в ../gigaam_transcriber/server/static (раздаётся FastAPI).
// dev-прокси на api (uvicorn :8000). В проде фронт и api — один origin за nginx;
// в dev же браузер шлёт Origin :5173, а сервер видит Host :8000 → серверный
// Origin-check (defense-in-depth) роняет мутации как «Bad Origin». Поэтому для
// dev переписываем Host+Origin на target, чтобы они совпали. Билд это не трогает.
const API_TARGET = "http://127.0.0.1:8000";
const devProxy = {
  target: API_TARGET,
  changeOrigin: true,
  configure: (proxy: { on: (e: string, cb: (r: { setHeader: (k: string, v: string) => void }) => void) => void }) => {
    proxy.on("proxyReq", (proxyReq) => proxyReq.setHeader("origin", API_TARGET));
  },
};

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: {
    outDir: path.resolve(__dirname, "../gigaam_transcriber/server/static"),
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    proxy: {
      "/api": devProxy,
      "/healthz": devProxy,
      "/readyz": devProxy,
    },
  },
});
