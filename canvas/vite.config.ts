import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the server package's static dir so `mdl serve` hosts the
// canvas with zero extra packaging steps.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../packages/server/src/mdl_server/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:4800",
    },
  },
});
