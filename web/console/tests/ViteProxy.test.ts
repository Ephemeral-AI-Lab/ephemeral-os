import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("Vite preview proxy", () => {
  it("proxies only preview routes and never source modules", async () => {
    const source = await readFile(join(resolve(process.cwd()), "vite.config.ts"), "utf8");

    expect(source).toContain('"/s/": {');
    expect(source).not.toContain('"/s": {');
  });
});
