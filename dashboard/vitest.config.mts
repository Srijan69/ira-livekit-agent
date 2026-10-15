import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const here = import.meta.dirname;

// Vitest needs its own config: electron.vite.config.ts describes three Electron
// builds, none of which is a test environment. Renderer code only here -- the
// main process is covered by the Python-side and manual checks, since driving a
// real Electron window is sub-project D's question, not this one's.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(here, "src"),
      "@shared": resolve(here, "shared"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "electron/**/*.test.ts"],
  },
});
