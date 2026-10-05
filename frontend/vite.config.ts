import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Builds one self-contained ES module that HA loads as the "lymow-panel" custom
// panel. React and the CSS are bundled in; the CSS is injected into the panel's
// shadow root, so nothing leaks into (or from) the rest of Home Assistant.
export default defineConfig({
  plugins: [react()],
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: "../custom_components/lymow/www",
    emptyOutDir: true,
    sourcemap: false,
    minify: true,
    rolldownOptions: { output: { minify: true } },
    lib: {
      entry: "src/main.tsx",
      formats: ["es"],
      fileName: () => "lymow-panel.js",
    },
  },
  test: { environment: "node" },
});
