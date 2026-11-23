import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// plan §11.3: dev server proxies /api and /ws to the backend.
// Default port 8001 matches compose.expose-backend.yml (host port 8000 is taken).
const BACKEND =
  process.env.VITE_BACKEND_URL ?? `http://localhost:${process.env.BACKEND_HOST_PORT ?? "8001"}`;

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/ws": { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
