import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ActionIcon,
  Button,
  Code,
  Divider,
  Drawer,
  Group,
  Modal,
  NavLink,
  Paper,
  ScrollArea,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { Trash2 } from "lucide-react";
import { rpc, sandboxScope } from "@/api/rpc";
import type { WorkspaceSessionDestroyed } from "@/api/types";
import type { WorkspaceSnapshot } from "@/api/observability";
import { useErrorToast } from "@/components/ErrorToast";

export function SessionSidebar({
  sandboxId,
  workspaces,
  selected,
  narrow,
  opened,
  onClose,
  onSelect,
}: {
  sandboxId: string;
  workspaces: WorkspaceSnapshot[];
  selected: string | null;
  narrow: boolean;
  opened: boolean;
  onClose: () => void;
  onSelect: (sessionId: string | null) => void;
}) {
  const [destroyTargetId, setDestroyTargetId] = useState<string | null>(null);
  const [destroyBusy, setDestroyBusy] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const queryClient = useQueryClient();
  const { showError } = useErrorToast();

  const destroyTarget = useMemo(
    () => workspaces.find((workspace) => workspace.workspace_id === destroyTargetId) ?? null,
    [destroyTargetId, workspaces],
  );
  const activeCommands = destroyTarget?.active_namespace_executions.length ?? 0;

  const refreshSnapshot = () =>
    queryClient.invalidateQueries({ queryKey: ["sandbox", sandboxId, "snapshot"] });

  const destroySession = async () => {
    if (
      !destroyTarget ||
      destroyBusy ||
      activeCommands > 0 ||
      confirmation !== destroyTarget.workspace_id
    ) {
      return;
    }
    setDestroyBusy(true);
    try {
      const output = await rpc<WorkspaceSessionDestroyed>(
        "destroy_workspace_session",
        sandboxScope(sandboxId),
        { workspace_session_id: destroyTarget.workspace_id },
      );
      setDestroyTargetId(null);
      setConfirmation("");
      if (selected === destroyTarget.workspace_id) onSelect(null);
      void refreshSnapshot();
      notifications.show({
        color: "success",
        title: "Workspace session destroyed",
        message: `${output.workspace_session_id} · ${output.evicted_upperdir_bytes} bytes evicted`,
      });
    } catch (error) {
      showError(error);
    } finally {
      setDestroyBusy(false);
    }
  };

  const sessions = (
    <SessionList
      workspaces={workspaces}
      selected={selected}
      narrow={narrow}
      onDestroy={(workspaceId) => {
        setConfirmation("");
        setDestroyTargetId(workspaceId);
      }}
      onSelect={onSelect}
    />
  );

  return (
    <>
      {narrow ? (
        <Drawer
          data-terminal-sessions-drawer
          opened={opened}
          onClose={onClose}
          position="left"
          size="18rem"
          title="Workspace sessions"
        >
          {sessions}
        </Drawer>
      ) : (
        <Paper
          component="aside"
          data-terminal-session-rail
          radius={0}
          withBorder
          style={{ display: "flex", flex: "0 0 16rem", flexDirection: "column", minHeight: 0 }}
        >
          {sessions}
        </Paper>
      )}

      <Modal
        opened={destroyTarget !== null}
        onClose={() => {
          if (!destroyBusy) setDestroyTargetId(null);
        }}
        closeButtonProps={{ "aria-label": "Close destroy workspace session", disabled: destroyBusy }}
        closeOnEscape={!destroyBusy}
        title="Destroy workspace session"
        centered
      >
        {destroyTarget ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void destroySession();
            }}
          >
            <Stack gap="md">
              <Text size="sm">
                This permanently discards unpublished changes in <Code>{destroyTarget.workspace_id}</Code>.
              </Text>
              <TextInput
                autoComplete="off"
                label="Type the workspace session ID to confirm"
                value={confirmation}
                disabled={destroyBusy}
                onChange={(event) => setConfirmation(event.target.value)}
                styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
              />
              <Group justify="flex-end">
                <Button variant="subtle" disabled={destroyBusy} onClick={() => setDestroyTargetId(null)}>
                  Cancel
                </Button>
                <Button
                  color="danger"
                  type="submit"
                  loading={destroyBusy}
                  disabled={confirmation !== destroyTarget.workspace_id || activeCommands > 0}
                  leftSection={<Trash2 size={14} />}
                >
                  Destroy session
                </Button>
              </Group>
            </Stack>
          </form>
        ) : null}
      </Modal>
    </>
  );
}

function SessionList({
  workspaces,
  selected,
  narrow,
  onDestroy,
  onSelect,
}: {
  workspaces: WorkspaceSnapshot[];
  selected: string | null;
  narrow: boolean;
  onDestroy: (workspaceId: string) => void;
  onSelect: (sessionId: string | null) => void;
}) {
  return (
    <Stack gap={0} style={{ flex: 1, minHeight: 0 }}>
      {!narrow ? <Text fw={700} p="md" pb="sm" size="xs" tt="uppercase">Workspace sessions</Text> : null}
      <ScrollArea data-terminal-session-scroll style={{ flex: 1, minHeight: 0 }} type="auto">
        <Stack gap={2} px="sm" pb="sm">
          {workspaces.map((workspace) => {
            const activeCommands = workspace.active_namespace_executions.length;
            const destroyLabel = `Destroy workspace session ${workspace.workspace_id}`;
            return (
              <Group key={workspace.workspace_id} gap={4} wrap="nowrap">
                <NavLink
                  active={selected === workspace.workspace_id}
                  aria-label={`Filter commands by workspace session ${workspace.workspace_id}`}
                  description={`${workspace.network_profile} · ${workspace.layers.layer_count} layers`}
                  label={workspace.workspace_id}
                  onClick={() => onSelect(workspace.workspace_id)}
                  title={workspace.workspace_id}
                  styles={{
                    root: { flex: 1, minWidth: 0 },
                    label: {
                      fontFamily: "var(--mantine-font-family-monospace)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    },
                  }}
                />
                <ActionIcon
                  aria-label={destroyLabel}
                  color="danger"
                  disabled={activeCommands > 0}
                  onClick={() => onDestroy(workspace.workspace_id)}
                  size={44}
                  title={
                    activeCommands > 0
                      ? `Stop ${activeCommands === 1 ? "the active command" : `${activeCommands} active commands`} first`
                      : destroyLabel
                  }
                  variant="subtle"
                >
                  <Trash2 aria-hidden="true" size={16} />
                </ActionIcon>
              </Group>
            );
          })}
          {workspaces.length === 0 ? (
            <Text c="dimmed" p="sm" size="xs">
              No live sessions. Create a retained session from the command bar when commands need to share private changes.
            </Text>
          ) : null}
          <Divider my="xs" />
          <NavLink
            active={selected === null}
            description="unfiltered ledger"
            label="all commands"
            onClick={() => onSelect(null)}
          />
        </Stack>
      </ScrollArea>
    </Stack>
  );
}
