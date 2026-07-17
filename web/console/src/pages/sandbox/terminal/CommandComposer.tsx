import { useEffect, useRef, useState } from "react";
import { Badge, Box, Button, Group, Paper, Text, TextInput } from "@mantine/core";
import { Play } from "lucide-react";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput } from "@/api/types";
import type { WorkspaceSnapshot } from "@/api/observability";
import { useErrorToast } from "@/components/ErrorToast";

export function CommandComposer({
  sandboxId,
  workspaceSessionId,
  workspace,
  onLaunched,
}: {
  sandboxId: string;
  workspaceSessionId: string | null;
  workspace: WorkspaceSnapshot | null;
  onLaunched: (cmd: string, workspaceSessionId: string | null, output: CommandOutput) => void;
}) {
  const [cmd, setCmd] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("300");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { showError } = useErrorToast();

  const timeout = Number(timeoutSeconds);
  const timeoutError = !Number.isInteger(timeout) || timeout < 1 || timeout > 86_400
    ? "Enter 1–86400 seconds."
    : null;
  const contextLabel = workspaceSessionId ? `Command in ${workspaceSessionId}` : "Quick run";
  const contextDescription = workspaceSessionId
    ? workspace
      ? `${workspace.network_profile} · ${workspace.layers.layer_count} ${workspace.layers.layer_count === 1 ? "layer" : "layers"}`
      : "retained session"
    : "shared · auto-publish";

  useEffect(() => {
    inputRef.current?.focus();
  }, [workspaceSessionId]);

  const submit = async () => {
    const text = cmd.trim();
    if (text === "" || busy || timeoutError) return;
    const args: Record<string, unknown> = {
      cmd: text,
      timeout_ms: timeout * 1000,
      yield_time_ms: 0,
    };
    if (workspaceSessionId) args["workspace_session_id"] = workspaceSessionId;
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
      <Group justify="space-between" mb="xs" wrap="wrap">
        <Group gap="xs" miw={0} wrap="wrap">
          <Text fw={500} size="sm" truncate>
            {contextLabel}
          </Text>
          <Badge size="sm" variant="light">
            {contextDescription}
          </Badge>
        </Group>
        <Text c="dimmed" size="xs">Enter to run</Text>
      </Group>
      <Group align="flex-end" gap="sm" wrap="wrap">
        <Box style={{ flex: "1 1 20rem", minWidth: 0 }}>
          <TextInput
            ref={inputRef}
            aria-label="Command"
            label="Command"
            value={cmd}
            onChange={(event) => setCmd(event.target.value)}
            placeholder={workspaceSessionId ? `Run in ${workspaceSessionId}…` : "Run once and publish…"}
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
            autoFocus
          />
        </Box>
        <TextInput
          aria-label="Timeout in seconds"
          data-terminal-timeout
          error={timeoutError}
          inputMode="numeric"
          label="Timeout"
          max={86_400}
          min={1}
          rightSection={<Text c="dimmed" size="xs">s</Text>}
          rightSectionPointerEvents="none"
          step={1}
          type="number"
          value={timeoutSeconds}
          w={112}
          onChange={(event) => setTimeoutSeconds(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            inputRef.current?.focus();
          }}
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
