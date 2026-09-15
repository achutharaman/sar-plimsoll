import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs locally (make run-api); in production Firebase Hosting rewrites
// /v1 and /admin to the Cloud Run API, so the browser always talks to its own origin (no CORS).
const api = process.env.PLIMSOLL_API_URL ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/v1": { target: api, changeOrigin: true },
      "/admin": { target: api, changeOrigin: true },
      "/health": { target: api, changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
