import type { WorkflowEntityRunStatus } from "@eos/contracts";

import { iterationDirName, type WorkflowContext } from "./paths.js";

/** One row of the tree-listing overview (§2.9). */
export interface ContextListingRow {
  path: string;
  status: WorkflowEntityRunStatus;
  /** First line of the owning entity's summary field, where one exists. */
  summary?: string;
}

/**
 * The subtree listing for one directory path. Collapse rules (§2.16):
 * prior (non-latest) iterations and entities under `archived/` subtrees
 * appear as their status row only, unless the listing root is already
 * inside the collapsed scope - their files stay readable at full fidelity.
 */
export function listContextSubtree(
  context: WorkflowContext,
  dirPath: string,
): ContextListingRow[] {
  const prefix = dirPath === "" ? "" : `${dirPath}/`;
  const rows = new Map<string, ContextListingRow>();

  const include = (path: string, row: ContextListingRow): void => {
    if (path === dirPath) return;
    if (prefix !== "" && !path.startsWith(prefix)) return;
    if (prefix === "" && path === "") return;
    rows.set(path, row);
  };

  const latestDir = context.latestIterationId
    ? iterationDirName(context.latestIterationId)
    : null;
  const insideScope = (scope: string): boolean =>
    dirPath === scope || dirPath.startsWith(`${scope}/`);

  const collapseTarget = (path: string): string | null => {
    // Prior-iteration collapse: anything under a non-latest live iteration
    // folder collapses to the iteration row (Phase 05 §2.20 rule).
    const segments = path.split("/");
    const head = segments[0] ?? "";
    if (
      head.startsWith("iteration_") &&
      latestDir !== null &&
      head !== latestDir &&
      !insideScope(head)
    ) {
      return head;
    }
    // Archive collapse: rows under `archived/` collapse to the archived
    // entity folder directly beneath the `archived` segment.
    const archivedIndex = segments.indexOf("archived");
    if (archivedIndex === -1) return null;
    const entityDepth = archivedIndex + 2;
    const entityDir = segments.slice(0, entityDepth).join("/");
    if (segments.length <= archivedIndex + 1) return null;
    return insideScope(entityDir) ? null : entityDir;
  };

  const allPaths: { path: string; isDir: boolean }[] = [
    ...[...context.files.keys()].map((path) => ({ path, isDir: false })),
    ...[...context.directories.keys()].map((path) => ({ path, isDir: true })),
  ];

  for (const { path, isDir } of allPaths) {
    if (path === "") continue;
    const target = collapseTarget(path);
    if (target !== null) {
      const entry = context.directories.get(target);
      if (entry) {
        include(target, { path: target, status: entry.owner.status });
      }
      continue;
    }
    // The bare `archived/` folder is structure, not an entity row.
    if (isDir && path.split("/").at(-1) === "archived") continue;
    const entry = isDir ? context.directories.get(path) : context.files.get(path);
    if (!entry) continue;
    const row: ContextListingRow = { path, status: entry.owner.status };
    const archived = isDir
      ? context.directories.get(path)?.archived
      : pathIsArchived(path);
    if (!archived && entry.owner.summaryFirstLine !== null) {
      row.summary = entry.owner.summaryFirstLine;
    }
    include(path, row);
  }

  return [...rows.values()].sort((a, b) => a.path.localeCompare(b.path));
}

function pathIsArchived(path: string): boolean {
  return path.split("/").includes("archived");
}
