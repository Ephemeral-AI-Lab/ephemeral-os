import { access, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const spike = ["MantineCompatibility", "Spike"].join("");
const formPackage = ["@mantine", "form"].join("/");
const retiredDevPackages = [
  formPackage,
  ["axe", "core"].join("-"),
  ["post", "css"].join(""),
  ["post", "css", "preset", "mantine"].join("-"),
  ["post", "css", "simple", "vars"].join("-"),
];

type PackageJson = {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
};

describe("P11 dependency and migration-scaffolding cleanup", () => {
  it("keeps only pinned, approved Mantine packages and no retired direct roots", async () => {
    const root = resolve(process.cwd());
    const packageJson = JSON.parse(await readFile(join(root, "package.json"), "utf8")) as PackageJson;
    const dependencies = packageJson.dependencies ?? {};
    const devDependencies = packageJson.devDependencies ?? {};
    const allDependencies = { ...dependencies, ...devDependencies };

    expect(
      Object.entries(dependencies)
        .filter(([name]) => name.startsWith("@mantine/"))
        .sort(([left], [right]) => left.localeCompare(right)),
    ).toEqual([
      ["@mantine/core", "9.4.1"],
      ["@mantine/hooks", "9.4.1"],
      ["@mantine/notifications", "9.4.1"],
    ]);
    for (const dependency of retiredDevPackages) {
      expect(allDependencies).not.toHaveProperty(dependency);
    }

    const packageLock = JSON.parse(await readFile(join(root, "package-lock.json"), "utf8")) as {
      packages?: Record<string, PackageJson>;
    };
    const rootPackage = packageLock.packages?.[""];
    for (const dependency of retiredDevPackages) {
      expect(rootPackage?.dependencies ?? {}).not.toHaveProperty(dependency);
      expect(rootPackage?.devDependencies ?? {}).not.toHaveProperty(dependency);
    }
    expect(packageLock.packages).not.toHaveProperty(`node_modules/${formPackage}`);
  });

  it("removes the disposable P00 compatibility fixture and its entry points", async () => {
    const root = resolve(process.cwd());
    const fixtureFiles = [
      `${spike}.tsx`,
      `${spike}.test.tsx`,
      `${spike}Import.test.ts`,
      `${spike}Render.test.tsx`,
      "fixture.tsx",
      "index.html",
      `${["mantine", "compatibility", "spike"].join("-")}.css`,
    ];

    for (const file of fixtureFiles) {
      await expect(access(join(root, "tests/p00", file))).rejects.toThrow();
    }
    await expect(access(join(root, "tests/browser", `${spike}.spec.ts`))).rejects.toThrow();
  });
});
