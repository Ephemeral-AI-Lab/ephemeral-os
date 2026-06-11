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
  it("index.ts re-exports only the service, the composer seam, and port types", () => {
    expect(Object.keys(publicSurface).sort()).toEqual([
      "WorkflowService",
      "defaultComposeLaunchContext",
    ]);
  });

  it("no outside package imports @eos/workflow internals", () => {
    const offenders: string[] = [];
    for (const entry of readdirSync(PACKAGES_ROOT)) {
      const packageRoot = join(PACKAGES_ROOT, entry);
      if (!statSync(packageRoot).isDirectory() || entry === "workflow") continue;
      for (const file of sourceFiles(packageRoot)) {
        for (const specifier of importSpecifiers(file)) {
          if (
            specifier.startsWith("@eos/workflow/") ||
            specifier.includes("packages/workflow/src")
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
      workflow: ["iteration"],
      iteration: ["attempt", "workflow"],
      attempt: ["plan", "work-item", "iteration"],
      plan: ["work-item", "attempt"],
      "work-item": ["attempt"],
    };
    const sourceRoot = join(PACKAGES_ROOT, "workflow", "src");
    for (const [entity, adjacent] of Object.entries(allowed)) {
      const file = join(sourceRoot, entity, "transitions.ts");
      const transitionImports = importSpecifiers(file)
        .filter((specifier) => specifier.endsWith("/transitions.js"))
        .map((specifier) => {
          const segments = specifier.split("/");
          return segments[segments.length - 2];
        });
      expect(
        transitionImports.sort(),
        `${entity}/transitions.ts imports stay adjacent`,
      ).toEqual([...adjacent].sort());
    }
  });
});
