import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: (loadEnv(mode, ".", "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS").__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS ?? "")
      .split(",").map((host) => host.trim()).filter(Boolean),
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
}));
