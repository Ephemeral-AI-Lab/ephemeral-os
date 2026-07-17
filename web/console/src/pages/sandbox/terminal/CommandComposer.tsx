import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Play, SlidersHorizontal } from "lucide-react";
import { Box, Button, Group, Paper, Popover, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput, WorkspaceSessionCreated } from "@/api/types";
import type { WorkspaceSnapshot } from "@/api/observability";
import { useErrorToast } from "@/components/ErrorToast";
import {
  SessionTargetPicker,
  type CreatedSessionTarget,
  type NetworkProfile,
} from "@/pages/sandbox/terminal/SessionTargetPicker";

/**
 * The prompt line. Fires exec_command with `yield_time_ms: 0` — an
 * agent-tool affordance a polling browser never needs, so it is pinned and
 * hidden. An empty timeout omits `timeout_ms` entirely. The automatic target
 * omits `workspace_session_id`, creating the runtime's shared auto-publishing
 * session.
 */
export function CommandComposer({
  sandboxId,
  workspaces,
  onLaunched,
}: {
  sandboxId: string;
  workspaces: WorkspaceSnapshot[];
  onLaunched: (cmd: string, workspaceSessionId: string | null, output: CommandOutput) => void;
}) {
  const [cmd, setCmd] = useState("");
  const [workspaceSessionId, setWorkspaceSessionId] = useState<string | null>(null);
  const [createdSession, setCreatedSession] = useState<CreatedSessionTarget | null>(null);
  const [createBusy, setCreateBusy] = useState(false);
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const { showError } = useErrorToast();

  const timeout = timeoutSeconds.trim();
  const timeoutError = timeout !== "" && (!Number.isFinite(Number(timeout)) || Number(timeout) <= 0)
    ? "Enter a positive timeout."
    : null;

  useEffect(() => {
    if (
      createdSession &&
      workspaces.some((workspace) => workspace.workspace_id === createdSession.workspaceSessionId)
    ) {
      setCreatedSession(null);
    }
    if (
      workspaceSessionId &&
      createdSession?.workspaceSessionId !== workspaceSessionId &&
      !workspaces.some((workspace) => workspace.workspace_id === workspaceSessionId)
    ) {
      setWorkspaceSessionId(null);
    }
  }, [createdSession, workspaceSessionId, workspaces]);

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
      setWorkspaceSessionId(target.workspaceSessionId);
      void queryClient.invalidateQueries({ queryKey: ["sandbox", sandboxId, "snapshot"] });
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

  const submit = async () => {
    const text = cmd.trim();
    if (text === "" || busy || timeoutError) return;
    const args: Record<string, unknown> = { cmd: text, yield_time_ms: 0 };
    if (workspaceSessionId) args["workspace_session_id"] = workspaceSessionId;
    if (timeout !== "") {
      args["timeout_ms"] = Math.round(Number(timeout) * 1000);
    }
    setBusy(true);
    try {
      const output = await rpc<CommandOutput>(
        "exec_command",
        sandboxScope(sandboxId),
        args,
      );
      setCmd("");
      onLaunched(text, workspaceSessionId, output);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  return (
    <Paper
      component="form"
      data-terminal-composer
      px="md"
      py="sm"
      radius={0}
      withBorder
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <Group align="flex-end" gap="sm" wrap="wrap">
        <Box style={{ flex: "1 1 20rem", minWidth: 0 }}>
          <TextInput
            ref={inputRef}
            aria-label="Command"
            label="Command"
            value={cmd}
            onChange={(event) => setCmd(event.target.value)}
            placeholder="$ run a command…"
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
            autoFocus
          />
        </Box>
        <Box style={{ flex: "0 1 20rem" }} w={{ base: "100%", sm: 320 }}>
          <SessionTargetPicker
            createdSession={createdSession}
            creating={createBusy}
            value={workspaceSessionId}
            workspaces={workspaces}
            onChange={setWorkspaceSessionId}
            onCreate={(networkProfile) => void createSession(networkProfile)}
          />
        </Box>
        <Popover opened={optionsOpen} onChange={setOptionsOpen} position="top-end" width={248} withArrow>
          <Popover.Target>
            <Button
              aria-label={timeout === "" ? "Command options" : `Command options, timeout ${timeout} seconds`}
              color={timeoutError ? "danger" : undefined}
              leftSection={<SlidersHorizontal size={13} />}
              type="button"
              variant="default"
              onClick={() => setOptionsOpen((opened) => !opened)}
            >
              {timeout === "" ? "Options" : `Timeout ${timeout}s`}
            </Button>
          </Popover.Target>
          <Popover.Dropdown>
            <TextInput
              aria-label="Timeout in seconds"
              description="Leave empty to use the runtime default."
              error={timeoutError}
              inputMode="numeric"
              label="Timeout (seconds)"
              placeholder="No timeout"
              value={timeoutSeconds}
              onChange={(event) => setTimeoutSeconds(event.target.value)}
              styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
            />
          </Popover.Dropdown>
        </Popover>
        <Button
          type="submit"
          variant="filled"
          leftSection={<Play size={13} />}
          loading={busy}
          disabled={busy || cmd.trim() === "" || timeoutError !== null}
        >
          Run
        </Button>
      </Group>
    </Paper>
  );
}
