import { configDefaults, defineConfig } from "vitest/config";

/**
 * Live provider tests: network + real credentials, excluded from the unit
 * runner (`*.e2e.ts` is invisible to the default vitest include) and from
 * `pnpm run check`. Manual, laptop-only; never CI.
 */
export default defineConfig({
  test: {
    include: ["packages/*/e2e/**/*.e2e.ts"],
    exclude: [
      ...configDefaults.exclude,
      "packages/*/e2e/**/*cache*.e2e.ts",
      "packages/agent-runtime/e2e/tau-bench-lite.e2e.ts",
    ],
    testTimeout: 60_000,
    fileParallelism: false,
    retry: 0,
    passWithNoTests: true,
  },
});
