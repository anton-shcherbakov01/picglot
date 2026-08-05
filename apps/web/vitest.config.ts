import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // `e2e/` holds Playwright specs, run by `npm run test:e2e`. Vitest's default
    // glob sweeps up `*.spec.ts` and fails on them, so scope it to unit tests.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
