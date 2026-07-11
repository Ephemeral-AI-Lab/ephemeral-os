import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronUp, Folder } from "lucide-react";
import { Button, Modal, Text } from "@mantine/core";
import { listWorkspaceDirectories } from "@/api/hostResources";

export function WorkspacePicker({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState<string | null>(null);
  const listing = useQuery({
    queryKey: ["workspace-directories", path],
    queryFn: () => listWorkspaceDirectories(path),
    enabled: open,
  });
  const currentPath = listing.data?.path ?? null;

  return (
    <>
      <Button
        id={id}
        type="button"
        variant="outline"
        className="w-full justify-start font-mono"
        onClick={() => {
          setPath(value || null);
          setOpen(true);
        }}
      >
        <Folder size={13} className="shrink-0 text-accent" />
        <span className="min-w-0 truncate">{value || "Select a folder…"}</span>
      </Button>
      <Modal
        opened={open}
        onClose={() => setOpen(false)}
        title="Select workspace folder"
        centered
        size="lg"
      >
          <Text size="sm" c="dimmed" mb="md">
            Choose the host directory to bind-mount into the sandbox.
          </Text>
          <div className="flex min-h-0 flex-col gap-2">
            <div className="flex items-center gap-2 rounded border border-line bg-app p-2">
              <Button
                type="button"
                variant="subtle"
                size="compact-xs"
                disabled={listing.data?.parent === null || !listing.data}
                onClick={() => setPath(listing.data?.parent ?? null)}
              >
                <ChevronUp size={13} />
                Up
              </Button>
              <Button
                type="button"
                variant="subtle"
                size="compact-xs"
                disabled={path === null}
                onClick={() => setPath(null)}
              >
                Roots
              </Button>
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-mid">
                {currentPath ?? "Choose a folder"}
              </span>
            </div>
            <div className="max-h-64 overflow-y-auto rounded border border-line p-1">
              {listing.isPending ? (
                <p className="p-2 text-xs text-ink-faint">Loading folders…</p>
              ) : null}
              {listing.isError ? (
                <p className="p-2 text-xs text-danger">
                  {(listing.error as Error).message}
                </p>
              ) : null}
              {listing.data?.directories.length === 0 ? (
                <p className="p-2 text-xs text-ink-faint">No child folders.</p>
              ) : null}
              {listing.data?.directories.map((directory) => (
                <button
                  key={directory.path}
                  type="button"
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-surface-hover"
                  onClick={() => setPath(directory.path)}
                >
                  <Folder size={13} className="shrink-0 text-accent" />
                  <span className="min-w-0 flex-1 truncate font-mono">{directory.name}</span>
                </button>
              ))}
              {listing.data?.truncated ? (
                <p className="p-2 text-xs text-ink-faint">
                  Showing the first 500 child folders.
                </p>
              ) : null}
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="subtle" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                type="button"
                variant="filled"
                disabled={currentPath === null}
                onClick={() => {
                  if (currentPath === null) return;
                  onChange(currentPath);
                  setOpen(false);
                }}
              >
                Use this folder
              </Button>
            </div>
          </div>
      </Modal>
    </>
  );
}
