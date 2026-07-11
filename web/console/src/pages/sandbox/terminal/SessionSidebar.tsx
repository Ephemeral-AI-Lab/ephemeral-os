import { Drawer, NavLink, Paper, ScrollArea, Stack, Text } from "@mantine/core";
import type { WorkspaceSnapshot } from "@/api/observability";

export function SessionSidebar({
  workspaces,
  selected,
  narrow,
  opened,
  onClose,
  onSelect,
}: {
  workspaces: WorkspaceSnapshot[];
  selected: string | null;
  narrow: boolean;
  opened: boolean;
  onClose: () => void;
  onSelect: (sessionId: string | null) => void;
}) {
  const sessions = <SessionList workspaces={workspaces} selected={selected} onSelect={onSelect} />;

  if (narrow) {
    return (
      <Drawer
        data-terminal-sessions-drawer
        opened={opened}
        onClose={onClose}
        position="left"
        size="18rem"
        title="Session history"
      >
        {sessions}
      </Drawer>
    );
  }

  return (
    <Paper
      component="aside"
      data-terminal-session-rail
      radius={0}
      withBorder
      style={{ display: "flex", flex: "0 0 15rem", flexDirection: "column", minHeight: 0 }}
    >
      <Text fw={700} p="md" size="xs" tt="uppercase">Session history</Text>
      {sessions}
    </Paper>
  );
}

function SessionList({
  workspaces,
  selected,
  onSelect,
}: {
  workspaces: WorkspaceSnapshot[];
  selected: string | null;
  onSelect: (sessionId: string | null) => void;
}) {
  return (
    <ScrollArea data-terminal-session-scroll style={{ flex: 1, minHeight: 0 }} type="auto">
      <Stack gap={2} p="sm">
        <NavLink
          active={selected === null}
          description="unfiltered ledger"
          label="all"
          onClick={() => onSelect(null)}
          styles={{ label: { fontFamily: "var(--mantine-font-family-monospace)" } }}
        />
        {workspaces.map((workspace) => (
          <NavLink
            key={workspace.workspace_id}
            active={selected === workspace.workspace_id}
            description={`${workspace.network_profile} · ${workspace.layers.layer_count} layers`}
            label={workspace.workspace_id}
            onClick={() => onSelect(workspace.workspace_id)}
            styles={{ label: { fontFamily: "var(--mantine-font-family-monospace)" } }}
          />
        ))}
        {workspaces.length === 0 ? (
          <Text c="dimmed" p="sm" size="xs">
            No live sessions. Run a command below to publish one.
          </Text>
        ) : null}
      </Stack>
    </ScrollArea>
  );
}
