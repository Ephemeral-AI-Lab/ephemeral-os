import { randomUUID } from "node:crypto";
import { mkdir, readdir, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

import type { PursuitContext } from "./paths.js";

/**
 * The §2.17 post-commit disk mirror: re-render the whole §9 universe under
 * `<contextRoot>/pursuit_<id>/`, temp-file + atomic rename per file, and
 * prune paths that left the universe (a refocus relocation). The DB stays
 * authoritative; nothing in the package ever reads these files.
 */
export async function projectPursuitContextMirror(
  contextRoot: string,
  context: PursuitContext,
): Promise<void> {
  const root = join(contextRoot, context.rootPath);
  await mkdir(root, { recursive: true });

  for (const path of context.directories.keys()) {
    if (path === "") continue;
    await mkdir(join(root, path), { recursive: true });
  }

  for (const [path, entry] of context.files) {
    const target = join(root, path);
    await mkdir(dirname(target), { recursive: true });
    const temp = join(dirname(target), `.${randomUUID()}.tmp`);
    await writeFile(temp, entry.content, "utf8");
    await rename(temp, target);
  }

  await prune(root, "", context);
}

async function prune(
  root: string,
  relative: string,
  context: PursuitContext,
): Promise<void> {
  const entries = await readdir(join(root, relative), { withFileTypes: true });
  for (const entry of entries) {
    const childRelative = relative === "" ? entry.name : `${relative}/${entry.name}`;
    if (entry.isDirectory()) {
      if (!context.directories.has(childRelative)) {
        await rm(join(root, childRelative), { recursive: true, force: true });
        continue;
      }
      await prune(root, childRelative, context);
      continue;
    }
    if (!context.files.has(childRelative)) {
      await rm(join(root, childRelative), { force: true });
    }
  }
}
