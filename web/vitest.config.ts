import { defineConfig } from "vitest/config";

// Separate from vite.config.ts because the React Router plugin fails under Vitest.
export default defineConfig({
  test: {
    environment: "jsdom",
    // Testing Library's auto-cleanup needs a global afterEach; tests still import explicitly.
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    passWithNoTests: true,
  },
});
