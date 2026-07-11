import { useEffect, useMemo, useRef, useState } from "react";
import { useQueries, type UseQueryResult } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  File,
  FileSymlink,
  Folder,
  HelpCircle,
} from "lucide-react";
import { Box, Center, Loader, Text, UnstyledButton } from "@mantine/core";
import { fileList, type FileListEntry, type FileListResult } from "@/api/files";
import { formatBytes } from "@/lib/format";
import { useVirtualizer } from "@tanstack/react-virtual";

type TreeEntryRow = {
  type: "entry";
  entry: FileListEntry;
  path: string;
  parentPath: string;
  depth: number;
};

type TreeStatusRow = {
  type: "status";
  key: string;
  depth: number;
  message: string;
  tone: "dimmed" | "danger" | "warning";
};

type TreeRow = TreeEntryRow | TreeStatusRow;

const ROW_HEIGHT = 31;
const TYPEAHEAD_DELAY_MS = 700;

/**
 * A lazy, virtualized flat-tree composition. Directories are queried only
 * after expansion, while the visible rows remain one keyboard-navigable tree
 * even when a directory has the API's 2,000-entry listing cap.
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
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([""]));
  const [focusedPath, setFocusedPath] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef(new Map<string, HTMLButtonElement>());
  const pendingChildFocus = useRef<string | null>(null);
  const typeahead = useRef({ value: "", timeout: 0 });

  useEffect(() => {
    setExpanded(new Set([""]));
    setFocusedPath("");
  }, [sandboxId, session]);

  const expandedPaths = useMemo(() => [...expanded].sort(), [expanded]);
  const listings = useQueries({
    queries: expandedPaths.map((dirPath) => ({
      queryKey: ["files", sandboxId, session ?? "", "list", dirPath],
      queryFn: () => fileList(sandboxId, dirPath, session),
      staleTime: 5000,
    })),
  });
  const listingStateKey = listings
    .map((listing) => `${listing.status}:${listing.dataUpdatedAt}:${listing.errorUpdatedAt}`)
    .join("|");
  const listingsByPath = useMemo(
    () => new Map(expandedPaths.map((path, index) => [path, listings[index]])),
    [expandedPaths, listingStateKey],
  );

  const rows = useMemo(
    () => flattenRows("", 0, expanded, listingsByPath),
    [expanded, listingsByPath],
  );
  const entries = useMemo(
    () => rows.filter((row): row is TreeEntryRow => row.type === "entry"),
    [rows],
  );
  const rowIndexByPath = useMemo(
    () => new Map(rows.map((row, index) => [row.type === "entry" ? row.path : row.key, index])),
    [rows],
  );
  const entryIndexByPath = useMemo(
    () => new Map(entries.map((entry, index) => [entry.path, index])),
    [entries],
  );
  const rootTruncated = listingsByPath.get("")?.data?.truncated === true;
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });

  const focusPath = (path: string) => {
    const rowIndex = rowIndexByPath.get(path);
    if (rowIndex === undefined) return;
    pendingChildFocus.current = path;
    setFocusedPath(path);
    virtualizer.scrollToIndex(rowIndex, { align: "auto" });
    const element = rowRefs.current.get(path);
    if (element) {
      pendingChildFocus.current = null;
      element.focus();
    }
  };

  useEffect(() => {
    const parentPath = pendingChildFocus.current;
    if (parentPath === null) return;
    const child = entries.find((entry) => entry.parentPath === parentPath);
    if (!child) return;
    pendingChildFocus.current = null;
    focusPath(child.path);
  }, [entries]);

  const toggleDirectory = (path: string, focusFirstChild = false) => {
    const isOpen = expanded.has(path);
    if (!isOpen && focusFirstChild) pendingChildFocus.current = path;
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const activate = (row: TreeEntryRow) => {
    setFocusedPath(row.path);
    if (row.entry.kind === "directory") toggleDirectory(row.path);
    else if (row.entry.kind === "file") onSelect(row.path);
  };

  const onTreeKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, row: TreeEntryRow) => {
    const currentIndex = entryIndexByPath.get(row.path) ?? 0;
    const parentPath = row.parentPath;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        focusPath(entries[Math.min(entries.length - 1, currentIndex + 1)]?.path ?? row.path);
        return;
      case "ArrowUp":
        event.preventDefault();
        focusPath(entries[Math.max(0, currentIndex - 1)]?.path ?? row.path);
        return;
      case "Home":
        event.preventDefault();
        focusPath(entries[0]?.path ?? row.path);
        return;
      case "End":
        event.preventDefault();
        focusPath(entries.at(-1)?.path ?? row.path);
        return;
      case "ArrowRight":
        if (row.entry.kind !== "directory") return;
        event.preventDefault();
        if (!expanded.has(row.path)) toggleDirectory(row.path, true);
        else {
          const child = entries.find((entry) => entry.parentPath === row.path);
          if (child) focusPath(child.path);
        }
        return;
      case "ArrowLeft":
        if (row.entry.kind === "directory" && expanded.has(row.path)) {
          event.preventDefault();
          toggleDirectory(row.path);
          return;
        }
        if (parentPath) {
          event.preventDefault();
          focusPath(parentPath);
        }
        return;
      case "Enter":
      case " ":
        event.preventDefault();
        activate(row);
        return;
      default:
        break;
    }

    if (event.key.length !== 1 || event.metaKey || event.ctrlKey || event.altKey) return;
    const nextValue = `${typeahead.current.value}${event.key}`.toLocaleLowerCase();
    typeahead.current.value = nextValue;
    window.clearTimeout(typeahead.current.timeout);
    typeahead.current.timeout = window.setTimeout(() => {
      typeahead.current.value = "";
    }, TYPEAHEAD_DELAY_MS);
    const candidates = [...entries.slice(currentIndex + 1), ...entries.slice(0, currentIndex + 1)];
    const match = candidates.find((entry) => entry.entry.name.toLocaleLowerCase().startsWith(nextValue));
    if (match) {
      event.preventDefault();
      focusPath(match.path);
    }
  };

  if (rows.length === 0) {
    return (
      <Center h="100%" p="md">
        <Loader aria-label="Loading files" size="sm" />
      </Center>
    );
  }

  return (
    <Box data-files-tree style={{ display: "flex", flex: 1, minHeight: 0, flexDirection: "column" }}>
      {rootTruncated ? (
        <Text c="warning.8" data-files-tree-truncated px="sm" py="xs" size="xs">
          Showing the first 2,000 entries returned by this directory.
        </Text>
      ) : null}
      <Box
        aria-label="File tree"
        component="div"
        ref={scrollRef}
        role="tree"
        style={{ flex: 1, minHeight: 0, overflowY: "auto", position: "relative" }}
      >
        <Box h={virtualizer.getTotalSize()} pos="relative">
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = rows[virtualRow.index];
            return (
              <Box
                key={row.type === "entry" ? row.path : row.key}
                left={0}
                pos="absolute"
                right={0}
                style={{ transform: `translateY(${virtualRow.start}px)` }}
                top={0}
              >
                {row.type === "entry" ? (
                  <TreeItem
                    expanded={expanded.has(row.path)}
                    focused={focusedPath === row.path || (focusedPath === "" && entries[0]?.path === row.path)}
                    onClick={() => activate(row)}
                    onKeyDown={(event) => onTreeKeyDown(event, row)}
                    ref={(element) => {
                      if (element) {
                        rowRefs.current.set(row.path, element);
                        if (pendingChildFocus.current === row.path) {
                          pendingChildFocus.current = null;
                          element.focus();
                        }
                      } else {
                        rowRefs.current.delete(row.path);
                      }
                    }}
                    row={row}
                    selected={selectedPath === row.path}
                  />
                ) : (
                  <TreeStatus row={row} />
                )}
              </Box>
            );
          })}
        </Box>
      </Box>
    </Box>
  );
}

function flattenRows(
  dirPath: string,
  depth: number,
  expanded: Set<string>,
  listingsByPath: Map<string, UseQueryResult<FileListResult, Error> | undefined>,
): TreeRow[] {
  const listing = listingsByPath.get(dirPath);
  if (!listing || listing.isPending) {
    return [{ type: "status", key: `${dirPath}:loading`, depth, message: "loading…", tone: "dimmed" }];
  }
  if (listing.isError) {
    return [{ type: "status", key: `${dirPath}:error`, depth, message: listing.error.message, tone: "danger" }];
  }
  const entries = listing.data?.entries ?? [];
  const rows: TreeRow[] = [];
  const sorted = [...entries].sort((a, b) => {
    const dirRank = (entry: FileListEntry) => (entry.kind === "directory" ? 0 : 1);
    return dirRank(a) - dirRank(b) || a.name.localeCompare(b.name);
  });
  for (const entry of sorted) {
    const path = dirPath === "" ? entry.name : `${dirPath}/${entry.name}`;
    rows.push({ type: "entry", entry, path, parentPath: dirPath, depth });
    if (entry.kind === "directory" && expanded.has(path)) {
      rows.push(...flattenRows(path, depth + 1, expanded, listingsByPath));
    }
  }
  if (entries.length === 0) {
    rows.push({ type: "status", key: `${dirPath}:empty`, depth, message: "empty directory", tone: "dimmed" });
  }
  if (listing.data?.truncated) {
    rows.push({
      type: "status",
      key: `${dirPath}:truncated`,
      depth,
      message: "Showing the first 2,000 entries returned by this directory.",
      tone: "warning",
    });
  }
  return rows;
}

const TreeItem = ({
  row,
  expanded,
  focused,
  selected,
  onClick,
  onKeyDown,
  ref,
}: {
  row: TreeEntryRow;
  expanded: boolean;
  focused: boolean;
  selected: boolean;
  onClick: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
  ref: React.Ref<HTMLButtonElement>;
}) => {
  const isDirectory = row.entry.kind === "directory";
  const actionable = row.entry.kind === "directory" || row.entry.kind === "file";
  return (
    <UnstyledButton
      aria-expanded={isDirectory ? expanded : undefined}
      aria-level={row.depth + 1}
      aria-selected={selected}
      c={actionable ? undefined : "dimmed"}
      disabled={!actionable}
      display="flex"
      onClick={onClick}
      onFocus={() => undefined}
      onKeyDown={onKeyDown}
      px="xs"
      py={6}
      ref={ref}
      role="treeitem"
      style={{
        alignItems: "center",
        borderRadius: "var(--mantine-radius-sm)",
        gap: 6,
        minHeight: ROW_HEIGHT,
        paddingLeft: `${row.depth * 14 + 8}px`,
        textAlign: "left",
        width: "100%",
        background: selected ? "var(--mantine-color-eyeBlue-0)" : undefined,
      }}
      tabIndex={focused ? 0 : -1}
      title={row.path}
    >
      {isDirectory ? (
        expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />
      ) : (
        <Box w={13} />
      )}
      <NodeIcon kind={row.entry.kind} />
      <Text ff="monospace" fw={selected ? 600 : 400} size="xs" style={{ flex: 1, minWidth: 0 }} truncate>
        {row.entry.name}
      </Text>
      {row.entry.size !== null ? (
        <Text c="dimmed" size="xs">{formatBytes(row.entry.size)}</Text>
      ) : null}
    </UnstyledButton>
  );
};

function TreeStatus({ row }: { row: TreeStatusRow }) {
  const color = row.tone === "danger" ? "danger.7" : row.tone === "warning" ? "warning.8" : "dimmed";
  return (
    <Text c={color} p="xs" pl={row.depth * 14 + 8} size="xs">
      {row.message}
    </Text>
  );
}

function NodeIcon({ kind }: { kind: FileListEntry["kind"] }) {
  const props = { size: 13, "aria-hidden": true };
  switch (kind) {
    case "directory":
      return <Folder {...props} color="var(--mantine-color-eyeBlue-6)" />;
    case "file":
      return <File {...props} color="var(--mantine-color-dimmed)" />;
    case "symlink":
      return <FileSymlink {...props} color="var(--mantine-color-dimmed)" />;
    default:
      return <HelpCircle {...props} color="var(--mantine-color-dimmed)" />;
  }
}
