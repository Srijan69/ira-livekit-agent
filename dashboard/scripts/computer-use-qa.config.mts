import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
const root = resolve(import.meta.dirname, "..");
export default defineConfig({ root, base: "./", plugins: [react(), tailwindcss()], resolve: { alias: { "@": resolve(root, "src"), "@shared": resolve(root, "shared") } }, build: { outDir: "shots/computer-use-renderer", emptyOutDir: true, rollupOptions: { input: resolve(root, "scripts/computer-use-qa.html") } } });
