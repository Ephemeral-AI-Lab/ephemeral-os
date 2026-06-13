import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import * as publicSurface from "../src/index.js";

const PACKAGES_ROOT = join(import.meta.dirname, "..", "..");

function sourceFiles(root: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    const full = join(root, entry.name);
    if (entry.isDirectory()) {
      files.push(...sourceFiles(full));
    } else if (entry.name.endsWith(".ts")) {
      files.push(full);
    }
  }
  return files;
}

function importSpecifiers(file: string): string[] {
  const text = readFileSync(file, "utf8");
  return [...text.matchAll(/from\s+"([^"]+)"/g)].map((match) => match[1]);
}

describe("package boundary (§16 case 14)", () => {
  it("index.ts re-exports the service factory, the composer seam, and host-facing schemas", () => {
    expect(Object.keys(publicSurface).sort()).toEqual([
      "ContextScriptOutputSchema",
      "CreatePursuitInputSchema",
      "InitialUserMessageSchema",
      "defaultComposeLaunchContext",
      "openPursuitService",
    ]);
  });

  it("no outside package imports @eos/pursuit internals", () => {
    const offenders: string[] = [];
    for (const entry of readdirSync(PACKAGES_ROOT)) {
      const packageRoot = join(PACKAGES_ROOT, entry);
      if (!statSync(packageRoot).isDirectory() || entry === "pursuit") continue;
      for (const file of sourceFiles(packageRoot)) {
        for (const specifier of importSpecifiers(file)) {
          if (
            specifier.startsWith("@eos/pursuit/") ||
            specifier.includes("packages/pursuit/src")
          ) {
            offenders.push(`${file} -> ${specifier}`);
          }
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("transition-module imports are adjacency-only", () => {
    const allowed: Record<string, string[]> = {
      pursuit: ["leg"],
      leg: ["attempt", "pursuit"],
      attempt: ["plan", "work-item", "leg"],
      plan: ["work-item", "attempt"],
      "work-item": ["attempt"],
    };
    const sourceRoot = join(PACKAGES_ROOT, "pursuit", "src");
    for (const [entity, adjacent] of Object.entries(allowed)) {
      const file = join(sourceRoot, entity, "transition.ts");
      const transitionImports = importSpecifiers(file)
        .filter((specifier) => specifier.endsWith("/transition.js"))
        .map((specifier) => {
          const segments = specifier.split("/");
          return segments[segments.length - 2];
        })
        .filter((specifier) => specifier in allowed);
      expect(
        transitionImports.sort(),
        `${entity}/transition.ts imports stay adjacent`,
      ).toEqual([...adjacent].sort());
    }
  });
});
