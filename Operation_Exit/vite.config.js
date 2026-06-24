import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    // Browser calls relative /api/* URLs; in dev they reach the Express dev server.
    proxy: { "/api": "http://localhost:3001" }
  },
  preview: {
    host: "0.0.0.0"
  }
});
