import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  File,
  FileSymlink,
  Folder,
  HelpCircle,
} from "lucide-react";
import { fileList, type FileListEntry } from "@/api/files";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/cn";

/**
 * Lazy directory tree over the daemon HTTP file list endpoint: each expanded
 * directory fetches one level in the active scope.
 */
export function FileTree({
  sandboxId,
  session,
  selectedPath,
  onSelect,
}: {
  sandboxId: string;
  session: string | null;
  selectedPath: string;
  onSelect: (path: string) => void;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-1">
      <DirLevel
        sandboxId={sandboxId}
        session={session}
        dirPath=""
        depth={0}
        selectedPath={selectedPath}
        onSelect={onSelect}
      />
    </div>
  );
}

function DirLevel({
  sandboxId,
  session,
  dirPath,
  depth,
  selectedPath,
  onSelect,
}: {
  sandboxId: string;
  session: string | null;
  dirPath: string;
  depth: number;
  selectedPath: string;
  onSelect: (path: string) => void;
}) {
  const listing = useQuery({
    queryKey: ["files", sandboxId, session ?? "", "list", dirPath],
    queryFn: () => fileList(sandboxId, dirPath, session),
    staleTime: 5000,
  });

  if (listing.isPending) {
    return (
      <div
        className="animate-pulse py-1 text-[11px] text-ink-faint"
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        loading…
      </div>
    );
  }
  if (listing.isError) {
    return (
      <div
        className="py-1 text-[11px] text-danger"
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        {String((listing.error as Error).message)}
      </div>
    );
  }
  const entries = listing.data?.entries ?? [];
  if (entries.length === 0) {
    return (
      <div
        className="py-1 text-[11px] text-ink-faint"
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        empty directory
      </div>
    );
  }
  const sorted = [...entries].sort((a, b) => {
    const dirRank = (entry: FileListEntry) => (entry.kind === "directory" ? 0 : 1);
    return dirRank(a) - dirRank(b) || a.name.localeCompare(b.name);
  });
  return (
    <>
      {sorted.map((entry) => (
        <TreeNode
          key={entry.name}
          sandboxId={sandboxId}
          session={session}
          entry={entry}
          dirPath={dirPath}
          depth={depth}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      ))}
      {listing.data?.truncated ? (
        <div
          className="py-1 text-[11px] text-warn"
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
        >
          listing truncated at 2000 entries
        </div>
      ) : null}
    </>
  );
}

function TreeNode({
  sandboxId,
  session,
  entry,
  dirPath,
  depth,
  selectedPath,
  onSelect,
}: {
  sandboxId: string;
  session: string | null;
  entry: FileListEntry;
  dirPath: string;
  depth: number;
  selectedPath: string;
  onSelect: (path: string) => void;
}) {
  const path = dirPath === "" ? entry.name : `${dirPath}/${entry.name}`;
  const [open, setOpen] = useState(false);
  const isDir = entry.kind === "directory";
  const selected = selectedPath === path;

  return (
    <>
      <button
        type="button"
        onClick={() => {
          if (isDir) setOpen((current) => !current);
          else if (entry.kind === "file") onSelect(path);
        }}
        className={cn(
          "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs",
          selected ? "bg-accent-soft text-accent" : "hover:bg-surface-hover",
          entry.kind !== "file" && entry.kind !== "directory" && "text-ink-faint",
        )}
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
        title={path}
      >
        {isDir ? (
          open ? (
            <ChevronDown size={12} className="shrink-0 text-ink-faint" />
          ) : (
            <ChevronRight size={12} className="shrink-0 text-ink-faint" />
          )
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <NodeIcon kind={entry.kind} />
        <span className="min-w-0 flex-1 truncate font-mono">{entry.name}</span>
        {entry.size !== null ? (
          <span className="shrink-0 text-[10px] text-ink-faint">
            {formatBytes(entry.size)}
          </span>
        ) : null}
      </button>
      {isDir && open ? (
        <DirLevel
          sandboxId={sandboxId}
          session={session}
          dirPath={path}
          depth={depth + 1}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      ) : null}
    </>
  );
}

function NodeIcon({ kind }: { kind: FileListEntry["kind"] }) {
  const className = "shrink-0 text-ink-faint";
  switch (kind) {
    case "directory":
      return <Folder size={12} className={cn(className, "text-accent/70")} />;
    case "file":
      return <File size={12} className={className} />;
    case "symlink":
      return <FileSymlink size={12} className={className} />;
    default:
      return <HelpCircle size={12} className={className} />;
  }
}
