import { useRef, useState } from "react";
import { CornerDownLeft } from "lucide-react";
import { ActionIcon, Group, Paper, Text, TextInput } from "@mantine/core";
import { rpc, sandboxScope } from "@/api/rpc";
import { useErrorToast } from "@/components/ErrorToast";

export const CTRL_C = "\u0003";
export const CTRL_D = "\u0004";

/**
 * The terminal's input line while a command runs. Enter sends the line via
 * write_command_stdin (yield pinned to 0) followed by an immediate
 * transcript nudge so the program's reaction renders without waiting for
 * the next poll tick. Ctrl-C / Ctrl-D stop at the input after writing so
 * the enclosing terminal-frame shortcut cannot send a duplicate control
 * frame. The explicit buttons stay for discoverability.
 */
export function StdinBar({
  sandboxId,
  commandSessionId,
  nudge,
}: {
  sandboxId: string;
  commandSessionId: string;
  nudge: () => void;
}) {
  const [text, setText] = useState("");
  const controlPendingRef = useRef(false);
  const { showError } = useErrorToast();

  const write = async (stdin: string) => {
    try {
      await rpc("write_command_stdin", sandboxScope(sandboxId), {
        command_session_id: commandSessionId,
        stdin,
        yield_time_ms: 0,
      });
    } catch (error) {
      showError(error);
    } finally {
      nudge();
    }
  };

  const submitLine = () => {
    const line = text;
    setText("");
    void write(`${line}\n`);
  };

  const sendControl = (control: typeof CTRL_C | typeof CTRL_D) => {
    if (controlPendingRef.current) return;
    controlPendingRef.current = true;
    void write(control).finally(() => {
      controlPendingRef.current = false;
    });
  };

  return (
    <Paper
      component="form"
      data-terminal-stdin
      p="xs"
      radius={0}
      withBorder
      onSubmit={(event) => {
        event.preventDefault();
        submitLine();
      }}
    >
      <Group gap="xs" wrap="nowrap">
      <Text c="dimmed" ff="monospace" size="xs">stdin</Text>
      <TextInput
        aria-label="Standard input"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            submitLine();
          } else if (event.key === "c" && event.ctrlKey) {
            event.preventDefault();
            event.stopPropagation();
            if (!event.repeat) sendControl(CTRL_C);
          } else if (event.key === "d" && event.ctrlKey) {
            event.preventDefault();
            event.stopPropagation();
            if (!event.repeat) sendControl(CTRL_D);
          }
        }}
        placeholder="type a line, Enter sends it"
        style={{ flex: 1, minWidth: 0 }}
        styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
      />
      <ActionIcon aria-label="Send line" type="submit" variant="subtle" title="send line">
        <CornerDownLeft size={12} />
      </ActionIcon>
      <ActionIcon
        aria-label="Send Ctrl-C"
        variant="subtle"
        onClick={() => sendControl(CTRL_C)}
        title="send Ctrl-C (SIGINT)"
        styles={{ root: { fontFamily: "var(--mantine-font-family-monospace)" } }}
      >
        ^C
      </ActionIcon>
      <ActionIcon
        aria-label="Send Ctrl-D"
        variant="subtle"
        onClick={() => sendControl(CTRL_D)}
        title="send Ctrl-D (EOF)"
        styles={{ root: { fontFamily: "var(--mantine-font-family-monospace)" } }}
      >
        ^D
      </ActionIcon>
      </Group>
    </Paper>
  );
}
