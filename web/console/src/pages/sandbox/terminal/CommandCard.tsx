import { useCallback, useRef } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Box, Collapse, Group, Loader, Paper, Text, UnstyledButton } from "@mantine/core";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput } from "@/api/types";
import { PortPreview } from "@/components/PortPreview";
import { StateBadge } from "@/components/StateBadge";
import { formatDuration } from "@/lib/format";
import type { LedgerEntry } from "@/pages/sandbox/terminal/ledger";
import { CTRL_C, CTRL_D, StdinBar } from "@/pages/sandbox/terminal/StdinBar";
import { TranscriptViewer } from "@/pages/sandbox/terminal/TranscriptViewer";

/**
 * One terminal per command. Expanded it is a terminal frame — transcript
 * filling the pane, input line integrated at the bottom, tail-pinned
 * autoscroll; collapsed it is a one-line ledger row. Addressable as
 * `#cmd-<command-session-id>`. Line discipline only — no PTY/raw mode.
 */
export function CommandCard({
  sandboxId,
  entry,
  expanded,
  onToggle,
  onUpdate,
  previewScopes,
}: {
  sandboxId: string;
  entry: LedgerEntry;
  expanded: boolean;
  onToggle: () => void;
  onUpdate: (patch: Partial<LedgerEntry>) => void;
  previewScopes: { id: string; label: string; isolated: boolean }[];
}) {
  const nudgeRef = useRef<() => void>(() => {});
  const controlPendingRef = useRef(false);
  const running = entry.status === "running";

  const onTerminal = useCallback(
    (output: CommandOutput) => {
      if (entry.status === "running" && output.status !== "running") {
        onUpdate({
          status: output.status,
          exitCode: output.exit_code,
          endedAt: Date.now(),
          publishRejected: output.publish_rejected === true,
          publishRejectClass: output.publish_reject_class ?? null,
        });
      }
    },
    [entry.status, onUpdate],
  );

  const registerNudge = useCallback((nudge: () => void) => {
    nudgeRef.current = nudge;
  }, []);

  const elapsedSeconds =
    ((entry.endedAt ?? Date.now()) - entry.startedAt) / 1000;

  const frameKeyDown = (event: React.KeyboardEvent) => {
    if (!running || !entry.commandSessionId || event.repeat) return;
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed) return;
    if (event.ctrlKey && (event.key === "c" || event.key === "d")) {
      event.preventDefault();
      if (controlPendingRef.current) return;
      controlPendingRef.current = true;
      void rpc("write_command_stdin", sandboxScope(sandboxId), {
        command_session_id: entry.commandSessionId,
        stdin: event.key === "c" ? CTRL_C : CTRL_D,
        yield_time_ms: 0,
      }).finally(() => {
        controlPendingRef.current = false;
        nudgeRef.current();
      });
    }
  };

  return (
    <Paper
      component="article"
      data-terminal-command
      id={entry.commandSessionId ? `cmd-${entry.commandSessionId}` : undefined}
      p={0}
      withBorder
      style={expanded ? { borderColor: "var(--mantine-color-eyeBlue-5)" } : undefined}
    >
      <UnstyledButton
        aria-controls={entry.commandSessionId ? `terminal-${entry.commandSessionId}` : undefined}
        aria-expanded={expanded}
        aria-label={`${expanded ? "Collapse" : "Expand"} command ${entry.cmd}`}
        data-terminal-command-toggle
        type="button"
        onClick={onToggle}
        p="sm"
        style={{ width: "100%" }}
      >
        <Group gap="sm" wrap="nowrap">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <Text c="dimmed" ff="monospace" size="xs">$</Text>
          <Text ff="monospace" size="xs" truncate style={{ flex: 1, minWidth: 0 }}>
            {entry.cmd}
          </Text>
          <Text
            c="dimmed"
            ff="monospace"
            size="xs"
            title="owning workspace session"
            truncate
            w={120}
          >
            {entry.autoPublish ? "auto-publish" : (entry.workspaceSessionId ?? "session ?")}
          </Text>
          {running ? (
            <Group gap={4} wrap="nowrap" style={{ color: "var(--mantine-color-eyeBlue-7)" }}>
              <Loader size={12} />
              <Text size="xs" style={{ color: "inherit" }}>{formatDuration(elapsedSeconds)}</Text>
            </Group>
          ) : entry.publishRejected ? (
            <StateBadge
              state="danger"
              label={`publish rejected${entry.publishRejectClass ? ` · ${entry.publishRejectClass}` : ""}`}
            />
          ) : (
            <StateBadge
              state={entry.status}
              label={entry.exitCode !== null ? `${entry.status} · exit ${entry.exitCode}` : entry.status}
            />
          )}
        </Group>
      </UnstyledButton>

      <Collapse expanded={expanded} keepMounted={false} transitionDuration={120}>
        <Box
          data-terminal-frame
          id={entry.commandSessionId ? `terminal-${entry.commandSessionId}` : undefined}
          tabIndex={0}
          onKeyDown={frameKeyDown}
          style={{ borderTop: "1px solid var(--mantine-color-neutral-3)" }}
        >
          {running ? (
            <Group justify="flex-end" p="xs" style={{ borderBottom: "1px solid var(--mantine-color-neutral-3)" }}>
              <PortPreview
                sandboxId={sandboxId}
                scopes={previewScopes}
                defaultScope={
                  entry.autoPublish || !entry.workspaceSessionId
                    ? "shared"
                    : (previewScopes.find((scope) => scope.id === entry.workspaceSessionId)
                        ?.id ?? "shared")
                }
              />
            </Group>
          ) : null}
          {expanded && entry.commandSessionId ? (
            <TranscriptViewer
              sandboxId={sandboxId}
              commandSessionId={entry.commandSessionId}
              running={running}
              onTerminal={onTerminal}
              registerNudge={registerNudge}
            />
          ) : expanded ? (
            <InlineTranscript output={entry.inlineOutput ?? ""} />
          ) : null}
          {expanded && running && entry.commandSessionId ? (
            <StdinBar
              sandboxId={sandboxId}
              commandSessionId={entry.commandSessionId}
              nudge={() => nudgeRef.current()}
            />
          ) : null}
        </Box>
      </Collapse>
    </Paper>
  );
}

/**
 * The inline path: a command that beat exec_command's initial wait returns
 * its transcript inline with no command_session_id — render it directly,
 * nothing to poll.
 */
function InlineTranscript({ output }: { output: string }) {
  return (
    <Box
      data-terminal-inline-transcript
      ff="monospace"
      p="sm"
      style={{ maxHeight: "16rem", overflowY: "auto" }}
    >
      {output.length > 0 ? (
        output.split("\n").map((line, index) => (
          <Text key={index} ff="monospace" size="xs" style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>
            {line}
          </Text>
        ))
      ) : (
        <Text c="dimmed" size="xs">no output</Text>
      )}
    </Box>
  );
}
