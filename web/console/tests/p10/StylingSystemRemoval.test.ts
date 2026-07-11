import { access, readdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const retired = ["tail", "wind"].join("");
const retiredVitePlugin = `@${retired}css/vite`;
const legacyThemeDirective = `@${"theme"}`;
const legacySourceDirective = `@${"source"}`;
const legacyColorVariable = `var(--${"color"}-`;

async function collectFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) return collectFiles(path);
      return /\.(?:css|ts|tsx)$/.test(entry.name) ? [path] : [];
    }),
  );
  return files.flat();
}

describe("P10 styling-system removal", () => {
  it("leaves Mantine as the only runtime visual and token system", async () => {
    const root = resolve(process.cwd());
    const files = [
      ...await collectFiles(join(root, "src")),
      ...await collectFiles(join(root, "tests/p00")),
      join(root, "vite.config.ts"),
    ];
    const sources = await Promise.all(files.map((file) => readFile(file, "utf8")));

    for (const source of sources) {
      expect(source).not.toContain(retired);
      expect(source).not.toContain(legacyThemeDirective);
      expect(source).not.toContain(legacySourceDirective);
      expect(source).not.toContain(legacyColorVariable);
    }

    const packageJson = JSON.parse(await readFile(join(root, "package.json"), "utf8")) as {
      dependencies?: Record<string, string>;
      devDependencies?: Record<string, string>;
    };
    const dependencyNames = [
      ...Object.keys(packageJson.dependencies ?? {}),
      ...Object.keys(packageJson.devDependencies ?? {}),
    ];
    expect(dependencyNames).not.toContain(retiredVitePlugin);
    expect(dependencyNames).not.toContain(`${retired}css`);

    const lockfile = await readFile(join(root, "package-lock.json"), "utf8");
    expect(lockfile).not.toContain(retired);
    await expect(access(join(root, "src/lib/cn.ts"))).rejects.toThrow();
    await expect(access(join(root, "tests/p02", `${retired}-allowlist.json`))).rejects.toThrow();
  });
});
