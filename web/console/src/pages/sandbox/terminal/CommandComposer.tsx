import { useRef, useState } from "react";
import { Play } from "lucide-react";
import { Box, Button, Group, Paper, Select, TextInput } from "@mantine/core";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput } from "@/api/types";
import type { WorkspaceSnapshot } from "@/api/observability";
import { useErrorToast } from "@/components/ErrorToast";

const AUTO_PUBLISH = "__auto_publish__";

/**
 * The prompt line. Fires exec_command with `yield_time_ms: 0` — an
 * agent-tool affordance a polling browser never needs, so it is pinned and
 * hidden. `timeout_ms` is semantic and stays user-visible, but optional:
 * an empty timeout omits the argument entirely (never 0). The "implicit"
 * target creates a one-shot session that captures and publishes on
 * completion, labelled auto-publish.
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
  const [target, setTarget] = useState<string>(AUTO_PUBLISH);
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { showError } = useErrorToast();

  const timeout = timeoutSeconds.trim();
  const timeoutError = timeout !== "" && (!Number.isFinite(Number(timeout)) || Number(timeout) <= 0)
    ? "Enter a positive timeout."
    : null;

  const submit = async () => {
    const text = cmd.trim();
    if (text === "" || busy || timeoutError) return;
    const workspaceSessionId = target === AUTO_PUBLISH ? null : target;
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
        <Select
          aria-label="Execution target"
          label="Execution target"
          value={target}
          w={{ base: "100%", sm: 224 }}
          onChange={(value) => setTarget(value ?? AUTO_PUBLISH)}
          data={[
            { value: AUTO_PUBLISH, label: "auto-publish" },
            ...workspaces.map((workspace) => ({
              value: workspace.workspace_id,
              label: `${workspace.workspace_id} (${workspace.network_profile})`,
            })),
          ]}
        />
        <TextInput
          aria-label="Timeout in seconds"
          error={timeoutError}
          inputMode="numeric"
          label="Timeout (seconds)"
          placeholder="none"
          value={timeoutSeconds}
          w={{ base: "100%", sm: 132 }}
          onChange={(event) => setTimeoutSeconds(event.target.value)}
          styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
        />
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
