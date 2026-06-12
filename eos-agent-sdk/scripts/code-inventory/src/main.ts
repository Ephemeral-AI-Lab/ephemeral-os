import * as fs from "node:fs/promises";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { writeInventory } from "./render.js";
import { scanWorkspace } from "./scan.js";

async function main(): Promise<void> {
  const workspaceRoot = await findWorkspaceRoot();
  const inventory = await scanWorkspace(workspaceRoot);
  await writeInventory(workspaceRoot, inventory);
  const outDir = path.join(workspaceRoot, "docs/code-inventory");
  console.log(
    `wrote ${String(inventory.stats.packages)} packages, ${String(inventory.stats.modules)} modules, ${String(inventory.stats.symbols)} symbols, and ${String(inventory.stats.relations)} relations to ${outDir}`,
  );
}

async function findWorkspaceRoot(): Promise<string> {
  const explicitRoot = process.argv.at(2);
  if (explicitRoot !== undefined) {
    return path.resolve(explicitRoot);
  }
  const start = path.dirname(fileURLToPath(import.meta.url));
  let dir = start;
  for (;;) {
    const packageJson = path.join(dir, "package.json");
    const srcDir = path.join(dir, "src");
    if (await exists(packageJson) && await exists(srcDir)) {
      return dir;
    }
    const next = path.dirname(dir);
    if (next === dir) {
      throw new Error(`could not find eos-agent-sdk root from ${start}`);
    }
    dir = next;
  }
}

async function exists(file: string): Promise<boolean> {
  try {
    await fs.access(file);
    return true;
  } catch {
    return false;
  }
}

await main();
