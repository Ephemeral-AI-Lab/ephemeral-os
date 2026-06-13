import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import * as publicSurface from "../../../src/workflows/pursuit/index.js";

const PACKAGE_ROOT = resolve(import.meta.dirname, "..", "..", "..");
const PURSUIT_ROOT = join(PACKAGE_ROOT, "src", "workflows", "pursuit");
const SRC_ROOT = join(PACKAGE_ROOT, "src");
const ALLOWED_EXTERNAL_PURSUIT_IMPORTS = new Set([
  join(PURSUIT_ROOT, "context-scripts.ts"),
  join(PURSUIT_ROOT, "index.ts"),
  join(PURSUIT_ROOT, "provider.ts"),
]);

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

describe("pursuit source boundary (§16 case 14)", () => {
  it("index.ts re-exports the service factory, the composer seam, and host-facing schemas", () => {
    expect(Object.keys(publicSurface).sort()).toEqual([
      "ContextScriptOutputSchema",
      "CreatePursuitInputSchema",
      "InitialUserMessageSchema",
      "defaultComposeLaunchContext",
      "openPursuitService",
    ]);
  });

  it("outside source imports pursuit only through its public edge files", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC_ROOT)) {
      if (resolve(file).startsWith(PURSUIT_ROOT)) continue;
      for (const specifier of importSpecifiers(file)) {
        if (specifier.startsWith("@eos/pursuit")) {
          offenders.push(`${file} -> ${specifier}`);
          continue;
        }
        if (specifier.startsWith(".")) {
          const target = resolve(dirname(file), specifier.replace(/\.js$/, ".ts"));
          if (
            target.startsWith(PURSUIT_ROOT) &&
            !ALLOWED_EXTERNAL_PURSUIT_IMPORTS.has(target)
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
    const sourceRoot = PURSUIT_ROOT;
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
