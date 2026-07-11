import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const consoleServer = process.env.SANDBOX_CONSOLE_BIND ?? "127.0.0.1:7880";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: `http://${consoleServer}`,
        changeOrigin: false,
      },
      "/s/": {
        target: `http://${consoleServer}`,
        changeOrigin: false,
        ws: true,
      },
    },
  },
});
