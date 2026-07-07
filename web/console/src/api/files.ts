import { rpc, sandboxScope } from "@/api/rpc";

export type FileEntryKind = "file" | "directory" | "symlink" | "other";

export interface FileListEntry {
  name: string;
  kind: FileEntryKind;
  size: number | null;
}

export interface FileListResult {
  path: string;
  entries: FileListEntry[];
  truncated: boolean;
}

export interface FileReadWindow {
  path: string;
  content: string;
  start_line: number;
  num_lines: number;
  total_lines: number;
  bytes_read: number;
  total_bytes: number;
  next_offset: number | null;
  truncated: boolean;
}

export interface BlameRange {
  start_line: number;
  line_count: number;
  owner: string;
}

export interface BlameResult {
  path: string;
  ranges: BlameRange[];
}

export function fileList(
  sandboxId: string,
  path: string,
  session: string | null,
): Promise<FileListResult> {
  const args: Record<string, unknown> = {};
  if (path !== "") args["path"] = path;
  if (session) args["workspace_session_id"] = session;
  return rpc<FileListResult>("file_list", sandboxScope(sandboxId), args);
}

export function fileRead(
  sandboxId: string,
  path: string,
  session: string | null,
  offset?: number,
  limit?: number,
): Promise<FileReadWindow> {
  const args: Record<string, unknown> = { path };
  if (session) args["workspace_session_id"] = session;
  if (offset !== undefined) args["offset"] = offset;
  if (limit !== undefined) args["limit"] = limit;
  return rpc<FileReadWindow>("file_read", sandboxScope(sandboxId), args);
}

export function fileBlame(sandboxId: string, path: string): Promise<BlameResult> {
  return rpc<BlameResult>("file_blame", sandboxScope(sandboxId), { path });
}

export function fileWrite(
  sandboxId: string,
  path: string,
  content: string,
  session: string | null,
): Promise<unknown> {
  const args: Record<string, unknown> = { path, content };
  if (session) args["workspace_session_id"] = session;
  return rpc("file_write", sandboxScope(sandboxId), args);
}

export interface WholeFile {
  content: string;
  totalLines: number;
  totalBytes: number;
}

/**
 * Page file_read to completion: the whole-file buffer FileEditor needs
 * before editing, since file_write replaces everything (saving a truncated
 * window would destroy the rest).
 */
export async function fileReadToEnd(
  sandboxId: string,
  path: string,
  session: string | null,
): Promise<WholeFile> {
  let content = "";
  let offset: number | undefined;
  let totalLines = 0;
  let totalBytes = 0;
  for (;;) {
    const window = await fileRead(sandboxId, path, session, offset, 2000);
    content = content === "" ? window.content : `${content}\n${window.content}`;
    totalLines = window.total_lines;
    totalBytes = window.total_bytes;
    if (window.next_offset === null || window.num_lines === 0) break;
    offset = window.next_offset;
  }
  return { content, totalLines, totalBytes };
}
