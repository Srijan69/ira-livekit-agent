import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

// Three independent builds. `externalizeDepsPlugin` keeps node_modules out of
// the main/preload bundles -- Electron can require them at runtime, and
// bundling `systeminformation` (which shells out to platform binaries) breaks
// it.
//
// Note there is deliberately no `"type": "module"` in package.json, so main
// and preload emit CommonJS. Electron refuses an ESM preload script when the
// renderer is sandboxed, and sandboxing is a hard requirement here: this app
// can start a process that controls the desktop.
export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: "out/main",
      lib: { entry: resolve(__dirname, "electron/main.ts") },
    },
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      outDir: "out/preload",
      lib: { entry: resolve(__dirname, "electron/preload.ts") },
    },
  },
  renderer: {
    root: "src",
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": resolve(__dirname, "src"),
        "@shared": resolve(__dirname, "shared"),
      },
    },
    build: {
      outDir: "out/renderer",
      rollupOptions: { input: resolve(__dirname, "src/index.html") },
    },
  },
});
