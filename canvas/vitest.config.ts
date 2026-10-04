import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component + interaction tests for the canvas (jsdom). Kept separate from the Vite
// app build; `npm test` runs these headless.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
