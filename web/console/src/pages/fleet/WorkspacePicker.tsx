import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronUp, Folder, Search } from "lucide-react";
import {
  Alert,
  Box,
  Button,
  Center,
  Combobox,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  useCombobox,
} from "@mantine/core";
import { listWorkspaceDirectories } from "@/api/hostResources";

export function WorkspacePicker({
  id,
  value,
  onChange,
  onOpenChange,
}: {
  id: string;
  value: string;
  onChange: (path: string) => void;
  onOpenChange?: (open: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const optionViewportRef = useRef<HTMLDivElement>(null);
  const combobox = useCombobox({
    onDropdownClose: () => {
      combobox.resetSelectedOption();
    },
    onDropdownOpen: () => {
      combobox.selectFirstOption();
      requestAnimationFrame(() => combobox.focusSearchInput());
    },
  });
  const listing = useQuery({
    queryKey: ["workspace-directories", path],
    queryFn: () => listWorkspaceDirectories(path),
    enabled: open,
  });
  const currentPath = listing.data?.path ?? null;
  const directories = listing.data?.directories ?? [];
  const filteredDirectories = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return directories;
    return directories.filter(
      (directory) =>
        directory.name.toLowerCase().includes(needle) ||
        directory.path.toLowerCase().includes(needle),
    );
  }, [directories, search]);
  const virtualizer = useVirtualizer({
    count: filteredDirectories.length,
    getScrollElement: () => optionViewportRef.current,
    estimateSize: () => 34,
    overscan: 8,
    initialRect: { width: 640, height: 256 },
  });

  useEffect(() => {
    optionViewportRef.current?.scrollTo({ top: 0 });
    combobox.updateSelectedOptionIndex();
  }, [combobox, filteredDirectories]);

  const navigateTo = (nextPath: string | null) => {
    setPath(nextPath);
    setSearch("");
    combobox.closeDropdown();
  };
  const closePicker = () => {
    combobox.closeDropdown();
    onOpenChange?.(false);
    setOpen(false);
  };

  return (
    <>
      <Button
        id={id}
        type="button"
        variant="outline"
        justify="flex-start"
        leftSection={<Folder size={14} />}
        onClick={() => {
          setPath(value || null);
          setSearch("");
          onOpenChange?.(true);
          setOpen(true);
        }}
      >
        <Text component="span" ff="monospace" truncate>{value || "Select a folder…"}</Text>
      </Button>
      <Modal
        opened={open}
        onClose={closePicker}
        closeButtonProps={{ "aria-label": "Close workspace picker" }}
        title="Select workspace folder"
        centered
        size="lg"
      >
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            Choose the host directory to bind-mount into the sandbox.
          </Text>
          <Group data-workspace-picker-navigation gap="xs" wrap="nowrap">
            <Button
              type="button"
              variant="subtle"
              size="compact-xs"
              disabled={listing.data?.parent === null || !listing.data}
              leftSection={<ChevronUp size={14} />}
              onClick={() => navigateTo(listing.data?.parent ?? null)}
            >
              Up
            </Button>
            <Button
              type="button"
              variant="subtle"
              size="compact-xs"
              disabled={path === null}
              onClick={() => navigateTo(null)}
            >
              Roots
            </Button>
            <Text ff="monospace" size="xs" title={currentPath ?? undefined} truncate style={{ flex: 1, minWidth: 0 }}>
              {currentPath ?? "Choose a folder"}
            </Text>
          </Group>

          {listing.isPending ? (
            <Center data-workspace-picker-loading py="md">
              <Group gap="xs">
                <Loader size="sm" />
                <Text c="dimmed" size="sm">Loading folders…</Text>
              </Group>
            </Center>
          ) : null}
          {listing.isError ? (
            <Alert color="danger" title="Folder listing unavailable" variant="light">
              {(listing.error as Error).message}
            </Alert>
          ) : null}
          {listing.data ? (
            <Combobox
              data-workspace-combobox
              store={combobox}
              withinPortal={false}
              onOptionSubmit={(nextPath) => navigateTo(nextPath)}
            >
              <Combobox.Target targetType="button">
                <Button
                  type="button"
                  variant="default"
                  justify="space-between"
                  rightSection={<Combobox.Chevron />}
                  leftSection={<Search size={14} />}
                  onClick={() => combobox.toggleDropdown()}
                >
                  Search child folders
                </Button>
              </Combobox.Target>
              <Combobox.Dropdown>
                <Combobox.Search
                  aria-label="Search child folders"
                  value={search}
                  onChange={(event) => {
                    setSearch(event.currentTarget.value);
                    combobox.openDropdown();
                  }}
                  placeholder="Filter the current directory"
                />
                <Combobox.Options>
                  {filteredDirectories.length === 0 ? (
                    <Combobox.Empty>
                      {directories.length === 0 ? "No child folders." : "No folders match this search."}
                    </Combobox.Empty>
                  ) : (
                    <Box data-workspace-option-viewport ref={optionViewportRef}>
                      <Box style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                        {virtualizer.getVirtualItems().map((virtualItem) => {
                          const directory = filteredDirectories[virtualItem.index];
                          return (
                            <Combobox.Option
                              data-workspace-folder-option
                              key={directory.path}
                              value={directory.path}
                              style={{
                                height: virtualItem.size,
                                left: 0,
                                position: "absolute",
                                top: 0,
                                transform: `translateY(${virtualItem.start}px)`,
                                width: "100%",
                              }}
                            >
                              <Group gap="xs" wrap="nowrap">
                                <Folder size={14} />
                                <Text ff="monospace" size="xs" truncate>
                                  {directory.name}
                                </Text>
                              </Group>
                            </Combobox.Option>
                          );
                        })}
                      </Box>
                    </Box>
                  )}
                </Combobox.Options>
              </Combobox.Dropdown>
            </Combobox>
          ) : null}
          {listing.data?.truncated ? (
            <Text c="dimmed" data-workspace-picker-truncated size="xs">
              Showing the first 500 child folders returned by the server.
            </Text>
          ) : null}
          <Group justify="flex-end">
            <Button type="button" variant="subtle" onClick={closePicker}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="filled"
              disabled={currentPath === null}
              onClick={() => {
                if (currentPath === null) return;
                onChange(currentPath);
                closePicker();
              }}
            >
              Use this folder
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
