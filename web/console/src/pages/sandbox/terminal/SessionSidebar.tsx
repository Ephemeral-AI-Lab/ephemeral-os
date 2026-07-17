import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ActionIcon,
  Button,
  Code,
  Divider,
  Drawer,
  Group,
  Menu,
  Modal,
  NavLink,
  Paper,
  ScrollArea,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { Globe2, Plus, Shield, Trash2 } from "lucide-react";
import { rpc, sandboxScope } from "@/api/rpc";
import type { WorkspaceSessionCreated, WorkspaceSessionDestroyed } from "@/api/types";
import type { WorkspaceSnapshot } from "@/api/observability";
import { useErrorToast } from "@/components/ErrorToast";

export type TerminalMode = "session" | "quick" | "all";

type NetworkProfile = "shared" | "isolated";

type CreatedSession = {
  workspaceSessionId: string;
  networkProfile: NetworkProfile;
};

export function SessionSidebar({
  sandboxId,
  workspaces,
  mode,
  selected,
  narrow,
  opened,
  onClose,
  onSelect,
}: {
  sandboxId: string;
  workspaces: WorkspaceSnapshot[];
  mode: TerminalMode;
  selected: string | null;
  narrow: boolean;
  opened: boolean;
  onClose: () => void;
  onSelect: (mode: TerminalMode, sessionId?: string) => void;
}) {
  const [createdSession, setCreatedSession] = useState<CreatedSession | null>(null);
  const [createBusy, setCreateBusy] = useState(false);
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

  useEffect(() => {
    if (
      createdSession &&
      workspaces.some((workspace) => workspace.workspace_id === createdSession.workspaceSessionId)
    ) {
      setCreatedSession(null);
    }
  }, [createdSession, workspaces]);

  const createSession = async (networkProfile: NetworkProfile) => {
    if (createBusy) return;
    setCreateBusy(true);
    try {
      const output = await rpc<WorkspaceSessionCreated>(
        "create_workspace_session",
        sandboxScope(sandboxId),
        { network_profile: networkProfile },
      );
      const target = {
        workspaceSessionId: output.workspace_session_id,
        networkProfile: output.network_profile,
      };
      setCreatedSession(target);
      onSelect("session", target.workspaceSessionId);
      void refreshSnapshot();
      notifications.show({
        color: "success",
        title: "Workspace session created",
        message: `${target.workspaceSessionId} · ${target.networkProfile}`,
      });
    } catch (error) {
      showError(error);
    } finally {
      setCreateBusy(false);
    }
  };

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
      if (mode === "session" && selected === destroyTarget.workspace_id) onSelect("quick");
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
      createdSession={createdSession}
      createBusy={createBusy}
      mode={mode}
      selected={selected}
      narrow={narrow}
      onDestroy={(workspaceId) => {
        setConfirmation("");
        setDestroyTargetId(workspaceId);
      }}
      onSelect={onSelect}
      onCreate={(networkProfile) => void createSession(networkProfile)}
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
  createdSession,
  createBusy,
  mode,
  selected,
  narrow,
  onDestroy,
  onCreate,
  onSelect,
}: {
  workspaces: WorkspaceSnapshot[];
  createdSession: CreatedSession | null;
  createBusy: boolean;
  mode: TerminalMode;
  selected: string | null;
  narrow: boolean;
  onDestroy: (workspaceId: string) => void;
  onCreate: (networkProfile: NetworkProfile) => void;
  onSelect: (mode: TerminalMode, sessionId?: string) => void;
}) {
  const sessions = workspaces.map((workspace) => ({
    workspaceSessionId: workspace.workspace_id,
    networkProfile: workspace.network_profile,
    layerCount: workspace.layers.layer_count,
    activeCommands: workspace.active_namespace_executions.length,
    persisted: true,
  }));
  if (
    createdSession &&
    !sessions.some((session) => session.workspaceSessionId === createdSession.workspaceSessionId)
  ) {
    sessions.push({
      ...createdSession,
      layerCount: 0,
      activeCommands: 0,
      persisted: false,
    });
  }

  return (
    <Stack gap={0} style={{ flex: 1, minHeight: 0 }}>
      <Group justify="space-between" p="md" pb="sm" wrap="nowrap">
        {!narrow ? <Text fw={700} size="xs" tt="uppercase">Workspace sessions</Text> : <span />}
        <Menu position="bottom-end" shadow="md" width={220}>
          <Menu.Target>
            <Button
              aria-label="New workspace session"
              leftSection={<Plus aria-hidden size={14} />}
              loading={createBusy}
              size="xs"
              variant="light"
            >
              New
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>Network profile</Menu.Label>
            <Menu.Item
              leftSection={<Globe2 aria-hidden size={14} />}
              onClick={() => onCreate("shared")}
            >
              <Text size="sm">Shared session</Text>
              <Text c="dimmed" size="xs">Sandbox network · retains changes</Text>
            </Menu.Item>
            <Menu.Item
              leftSection={<Shield aria-hidden size={14} />}
              onClick={() => onCreate("isolated")}
            >
              <Text size="sm">Isolated session</Text>
              <Text c="dimmed" size="xs">No external network · retains changes</Text>
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>
      <ScrollArea data-terminal-session-scroll style={{ flex: 1, minHeight: 0 }} type="auto">
        <Stack gap={2} px="sm" pb="sm">
          {sessions.map((session) => {
            const destroyLabel = `Destroy workspace session ${session.workspaceSessionId}`;
            return (
              <Group key={session.workspaceSessionId} gap={4} wrap="nowrap">
                <NavLink
                  active={mode === "session" && selected === session.workspaceSessionId}
                  aria-label={`Use workspace session ${session.workspaceSessionId}`}
                  description={`${session.networkProfile} · ${session.layerCount} ${session.layerCount === 1 ? "layer" : "layers"}`}
                  label={session.workspaceSessionId}
                  onClick={() => onSelect("session", session.workspaceSessionId)}
                  title={session.workspaceSessionId}
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
                {session.persisted ? (
                  <ActionIcon
                    aria-label={destroyLabel}
                    color="danger"
                    disabled={session.activeCommands > 0}
                    onClick={() => onDestroy(session.workspaceSessionId)}
                    size={44}
                    title={
                      session.activeCommands > 0
                        ? `Stop ${session.activeCommands === 1 ? "the active command" : `${session.activeCommands} active commands`} first`
                        : destroyLabel
                    }
                    variant="subtle"
                  >
                    <Trash2 aria-hidden="true" size={16} />
                  </ActionIcon>
                ) : null}
              </Group>
            );
          })}
          {sessions.length === 0 ? (
            <Text c="dimmed" p="sm" size="xs">
              No live sessions. Create one when commands need to share private changes.
            </Text>
          ) : null}
          <Divider my="xs" />
          <Text c="dimmed" px="sm" py={4} size="xs" tt="uppercase">Run once</Text>
          <NavLink
            active={mode === "quick"}
            description="shared · auto-publish"
            label="Quick run"
            onClick={() => onSelect("quick")}
          />
          <Text c="dimmed" mt="xs" px="sm" py={4} size="xs" tt="uppercase">History</Text>
          <NavLink
            active={mode === "all"}
            description="unfiltered ledger"
            label="All commands"
            onClick={() => onSelect("all")}
          />
        </Stack>
      </ScrollArea>
    </Stack>
  );
}
