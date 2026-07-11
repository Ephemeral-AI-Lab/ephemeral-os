import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { AppWindow } from "lucide-react";
import { Button, Input, Popover, Select, Stack, Text } from "@mantine/core";

export function previewPath(
  sandboxId: string,
  scope: string,
  port: string,
  path = "/",
): string {
  const params = new URLSearchParams({ scope, port, path });
  return `/sandboxes/${encodeURIComponent(sandboxId)}/preview?${params}`;
}

/**
 * The PortPreview launcher: pick a scope (shared, or an isolated workspace
 * session) and a port, then open the Preview tab pre-filled. Isolated scopes
 * carry the bind hint — the in-session server must listen on 0.0.0.0 or the
 * workspace IP, never 127.0.0.1.
 */
export function PortPreview({
  sandboxId,
  scopes,
  defaultScope = "shared",
  defaultPort = "",
  trigger,
}: {
  sandboxId: string;
  scopes?: { id: string; label: string; isolated: boolean }[];
  defaultScope?: string;
  defaultPort?: string;
  trigger?: ReactNode;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState(defaultScope);
  const [port, setPort] = useState(defaultPort);
  const scopeList = scopes ?? [
    { id: "shared", label: "shared network", isolated: false },
  ];
  const selected = scopeList.find((entry) => entry.id === scope);
  const portValid = /^\d+$/.test(port) && Number(port) >= 1 && Number(port) <= 65535;

  const openPreview = () => {
    if (!portValid) return;
    setOpen(false);
    void navigate(previewPath(sandboxId, scope, port));
  };

  return (
    <Popover opened={open} onChange={setOpen} position="bottom-end" withArrow>
      <Popover.Target>
        {trigger ?? (
          <Button size="compact-xs" title="Open a served port in the Preview tab">
            <AppWindow size={12} />
            Preview
          </Button>
        )}
      </Popover.Target>
      <Popover.Dropdown w={256}>
        <Stack gap="sm">
          <Text component="label" size="xs" c="dimmed">scope</Text>
          <Select
            value={scope}
            onChange={(value) => setScope(value ?? defaultScope)}
            data={scopeList.map((entry) => ({ value: entry.id, label: entry.label }))}
          />
          <Text component="label" size="xs" c="dimmed" mt={4}>port</Text>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              openPreview();
            }}
          >
            <Input
              value={port}
              onChange={(event) => setPort(event.target.value)}
              placeholder="5173"
              inputMode="numeric"
              w="100%"
              styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
              autoFocus
            />
          </form>
          {selected?.isolated ? (
            <Text fz={11} lh={1.333} c="warning">
              Isolated session: the server must bind 0.0.0.0 or the workspace
              IP — 127.0.0.1 is not reachable.
            </Text>
          ) : null}
          <Button
            variant="filled"
            size="compact-xs"
            mt={4}
            fullWidth
            disabled={!portValid}
            onClick={openPreview}
          >
            Open preview
          </Button>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
