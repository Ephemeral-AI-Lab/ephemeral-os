import type { ContextPage } from "@eos/contracts";

import {
  listContextSubtree,
  type ContextListingRow,
} from "./context-engine/projection/listing.js";
import type { PursuitContext } from "./context-engine/projection/paths.js";
import { resolveContextPath } from "./context-engine/projection/resolve.js";

const DEFAULT_CONTEXT_PAGE_BYTES = 16_384;

export type PursuitContextRead =
  | { kind: "page"; page: ContextPage }
  | { kind: "listing"; path: string; rows: ContextListingRow[] }
  | { kind: "error"; message: string };

/**
 * The read surface the deferred `read_pursuit_context` tool will bind: a
 * file path returns one byte-offset page of the latest render (overwrite
 * semantics - no revision pinning exists); a directory path returns the
 * subtree listing; an unknown path errors naming valid children.
 */
export function readPursuitContext(
  context: PursuitContext,
  params: { path?: string; offset?: number; maxBytes?: number } = {},
): PursuitContextRead {
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
