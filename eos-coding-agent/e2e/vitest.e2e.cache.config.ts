import { defineConfig } from "vitest/config";

/**
 * Explicit cache/benchmark e2e lane. These tests can run for much longer than
 * the standard live e2e suite, so they are triggered only by `test:e2e:cache`.
 */
export default defineConfig({
  test: {
    include: [
      "packages/*/e2e/**/*cache*.e2e.ts",
      "packages/agent-runtime/e2e/tau-bench-lite.e2e.ts",
    ],
    testTimeout: 60_000,
    fileParallelism: false,
    retry: 0,
    passWithNoTests: true,
  },
});
