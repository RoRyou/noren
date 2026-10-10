import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 開発時は API と WebSocket をローカルのバックエンド（8080）へ転送する
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/media": "http://127.0.0.1:8080",
      "/ws": { target: "ws://127.0.0.1:8080", ws: true },
    },
  },
});
