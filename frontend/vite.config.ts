import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// SPA билдится в ../gigaam_transcriber/server/static (раздаётся FastAPI).
// dev-прокси на api (uvicorn :8000), чтобы /api и cookie работали same-origin.
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
      "/api": "http://127.0.0.1:8000",
      "/healthz": "http://127.0.0.1:8000",
      "/readyz": "http://127.0.0.1:8000",
    },
  },
});
