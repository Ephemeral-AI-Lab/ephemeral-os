import type { ContextPage, WorkflowId } from "@eos/contracts";
import type { WorkflowDbReader } from "@eos/db";

import { listContextSubtree, type ContextListingRow } from "./archive/listing.js";
import { buildWorkflowContext, type WorkflowContext } from "./archive/paths.js";
import { resolveContextPath } from "./archive/resolve.js";
import { loadWorkflowTree } from "./workflow-tree.js";

export const DEFAULT_CONTEXT_PAGE_BYTES = 16_384;

export type WorkflowContextRead =
  | { kind: "page"; page: ContextPage }
  | { kind: "listing"; path: string; rows: ContextListingRow[] }
  | { kind: "error"; message: string };

/**
 * The context-path-universe load: derives the §9 universe from the latest
 * `WorkflowTree`. It never reads the disk mirror - the DB stays
 * authoritative and the mirror is a write-only cache.
 */
export async function loadWorkflowContext(
  db: WorkflowDbReader,
  workflowId: WorkflowId,
): Promise<WorkflowContext | null> {
  const tree = await loadWorkflowTree(db, workflowId);
  return tree ? buildWorkflowContext(tree) : null;
}

/**
 * The read surface the deferred `read_workflow_context` tool will bind: a
 * file path returns one byte-offset page of the latest render (overwrite
 * semantics - no revision pinning exists); a directory path returns the
 * subtree listing; an unknown path errors naming valid children.
 */
export function readWorkflowContext(
  context: WorkflowContext,
  params: { path?: string; offset?: number; maxBytes?: number } = {},
): WorkflowContextRead {
  const resolved = resolveContextPath(context, params.path);
  if (resolved.kind === "error") {
    return { kind: "error", message: resolved.message };
  }
  if (resolved.kind === "directory") {
    return {
      kind: "listing",
      path: resolved.path,
      rows: listContextSubtree(context, resolved.path),
    };
  }
  const bytes = Buffer.from(resolved.entry.content, "utf8");
  const offset = Math.max(0, params.offset ?? 0);
  const maxBytes = params.maxBytes ?? DEFAULT_CONTEXT_PAGE_BYTES;
  const slice = bytes.subarray(offset, offset + maxBytes);
  const next = offset + slice.length;
  const page: ContextPage = {
    path: resolved.path,
    status: resolved.entry.owner.status,
    total_bytes: bytes.length,
    offset,
    content: slice.toString("utf8"),
    ...(next < bytes.length && { next_offset: next }),
  };
  return { kind: "page", page };
}
