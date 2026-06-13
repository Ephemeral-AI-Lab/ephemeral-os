import type { ContextPage, ContextSearch } from "../contracts/pursuit.js";

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

export function searchPursuitContext(
  context: PursuitContext,
  params: { query: string; scope?: string },
): ContextSearch {
  const scope = normalizeContextPath(context, params.scope);
  const scopedToSuperseded = pathIsSuperseded(scope);
  const query = params.query.toLocaleLowerCase();
  const files: { path: string; status: ContextSearch["files"][number]["status"] }[] = [];
  const matches: {
    path: string;
    status: ContextSearch["matches"][number]["status"];
    field: string;
    snippet: string;
  }[] = [];

  for (const [path, entry] of context.files) {
    if (!withinScope(path, scope)) continue;
    if (!scopedToSuperseded && pathIsSuperseded(path)) continue;
    files.push({ path, status: entry.owner.status });
    const index = entry.content.toLocaleLowerCase().indexOf(query);
    if (index === -1) continue;
    matches.push({
      path,
      status: entry.owner.status,
      field: path.split("/").at(-1) ?? path,
      snippet: snippetAt(entry.content, index),
    });
  }

  return { files, matches };
}

function normalizeContextPath(context: PursuitContext, rawPath: string | undefined): string {
  const trimmed = (rawPath ?? "").replace(/^\/+|\/+$/g, "");
  if (trimmed === "" || trimmed === context.rootPath) return "";
  return trimmed.startsWith(`${context.rootPath}/`)
    ? trimmed.slice(context.rootPath.length + 1)
    : trimmed;
}

function withinScope(path: string, scope: string): boolean {
  return scope === "" || path === scope || path.startsWith(`${scope}/`);
}

function pathIsSuperseded(path: string): boolean {
  return path.split("/").includes("superseded");
}

function snippetAt(content: string, index: number): string {
  const start = Math.max(0, index - 40);
  const end = Math.min(content.length, index + 80);
  return content.slice(start, end);
}
