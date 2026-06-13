import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    passWithNoTests: true,
    include: ["packages/**/*.test.ts"],
    exclude: ["**/node_modules/**", "**/legacy/**", "**/legacy-tests/**"],
  },
});
