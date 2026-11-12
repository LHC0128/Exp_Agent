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
    // 默认按核数开 worker，与 pytest 同跑时占满 CPU 并把异步用例拖过超时；命令行 --maxWorkers 可临时覆盖。
    maxWorkers: 4,
    // 负载下 jsdom 渲染变慢，默认 5 s 余量不足；单个用例显式给出的超时仍优先。
    testTimeout: 15_000,
  },
}));
