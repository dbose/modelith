import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the server package's static dir so `mdl serve` hosts both
// SPAs with zero extra packaging steps. Two entries:
//   index.html -> the architect/engineer ER canvas (served at /)
//   sme.html   -> the SME glossary app          (served at /sme)
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../packages/server/src/mdl_server/static",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        sme: resolve(__dirname, "sme.html"),
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:4800",
    },
  },
});
