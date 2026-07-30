import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    // Proxying keeps the app same-origin in dev, so the API contract doesn't
    // change shape between dev and production.
    proxy: { "/api": "http://localhost:8000" },
  },
});
