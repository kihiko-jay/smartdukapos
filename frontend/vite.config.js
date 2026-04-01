import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  base: "./",   // IMPORTANT: relative paths for Electron file:// protocol
  build: {
    outDir:      "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: path.resolve(__dirname, "index.html"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true }
    }
  },
  test: {
    // Vitest config — uses jsdom so sessionStorage / window are available
    environment: "jsdom",
    globals: false,
    include: ["src/**/*.test.{js,jsx,ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include:  ["src/**"],
    },
  },
});

