import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by Flask at /app/ once built (backend/app.py), so every asset URL is
// prefixed with it. In dev, /api is proxied to the Flask server so the app
// talks to the same endpoints either way.
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:5050" },
  },
});
