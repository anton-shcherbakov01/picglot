import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  // The app imports through the `@/` alias tsconfig defines; vitest resolves
  // modules itself and knows nothing about it.
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    // `e2e/` holds Playwright specs, run by `npm run test:e2e`. Vitest's default
    // glob sweeps up `*.spec.ts` and fails on them, so scope it to unit tests.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
