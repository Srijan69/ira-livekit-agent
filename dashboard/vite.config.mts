import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

const here = import.meta.dirname;

/**
 * Renderer-only dev server, for working on the UI in a browser.
 *
 * `electron-vite dev` opens a native window, which is right for testing the
 * real thing but slow to iterate in and impossible to screenshot from a
 * browser tool. The renderer is an ordinary web page, so serving it directly
 * gives a fast loop for layout and animation work.
 *
 * `window.ira` is absent here, which is exactly why every component treats the
 * preload bridge as optional: the stats rings read "--" instead of throwing.
 */
export default defineConfig({
  root: "src",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": resolve(here, "src"),
      "@shared": resolve(here, "shared"),
    },
  },
  server: { port: 5273, strictPort: true },
});
