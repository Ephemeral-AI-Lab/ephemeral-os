import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Box, Button, Center, Flex, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { PanelLeft } from "lucide-react";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput } from "@/api/types";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { previewScopes } from "@/pages/sandbox/SandboxHeader";
import { CommandCard } from "@/pages/sandbox/terminal/CommandCard";
import { CommandComposer } from "@/pages/sandbox/terminal/CommandComposer";
import {
  SessionSidebar,
  type TerminalMode,
} from "@/pages/sandbox/terminal/SessionSidebar";
import { TranscriptPollProvider } from "@/pages/sandbox/terminal/TranscriptPollProvider";
import {
  entryFromExec,
  entryFromSnapshot,
  loadLedger,
  saveLedger,
  type LedgerEntry,
} from "@/pages/sandbox/terminal/ledger";

export function TerminalTab() {
  const { sandboxId, snapshot } = useSandbox();
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const queryClient = useQueryClient();

  const selectedSession = searchParams.get("session");
  const mode: TerminalMode = selectedSession
    ? "session"
    : searchParams.get("view") === "all"
      ? "all"
      : "quick";
  const [ledger, setLedger] = useState<LedgerEntry[]>(() => loadLedger(sandboxId));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const ledgerPaneRef = useRef<HTMLDivElement>(null);
  const finalizingRef = useRef<Set<string>>(new Set());
  const narrow = useMediaQuery("(max-width: 47.99em)");

  useEffect(() => {
    setLedger(loadLedger(sandboxId));
    setExpanded(new Set());
  }, [sandboxId]);

  useEffect(() => {
    saveLedger(sandboxId, ledger);
  }, [sandboxId, ledger]);

  const workspaces = useMemo(
    () => snapshot?.sandboxes[0]?.workspaces ?? [],
    [snapshot],
  );
  const selectedWorkspace = useMemo(
    () => workspaces.find((workspace) => workspace.workspace_id === selectedSession) ?? null,
    [selectedSession, workspaces],
  );

  const inFlight = useMemo(() => {
    const map = new Map<string, { workspaceId: string; command: string | null }>();
    for (const workspace of workspaces) {
      for (const execution of workspace.active_namespace_executions) {
        map.set(execution.namespace_execution_id, {
          workspaceId: workspace.workspace_id,
          command: execution.command ?? null,
        });
      }
    }
    return map;
  }, [workspaces]);

  useEffect(() => {
    if (!snapshot) return;
    setLedger((current) => {
      let changed = false;
      const reconciled = current.map((entry) => {
        if (!entry.commandSessionId) return entry;
        const execution = inFlight.get(entry.commandSessionId);
        if (!execution?.command || execution.command === entry.cmd) return entry;
        changed = true;
        return { ...entry, cmd: execution.command };
      });
      const known = new Set(
        reconciled
          .map((entry) => entry.commandSessionId)
          .filter((id): id is string => id !== null),
      );
      const additions: LedgerEntry[] = [];
      for (const [commandSessionId, execution] of inFlight) {
        if (!known.has(commandSessionId)) {
          additions.push(entryFromSnapshot(
            commandSessionId,
            execution.workspaceId,
            execution.command,
          ));
        }
      }
      return additions.length > 0 || changed ? [...reconciled, ...additions] : current;
    });
  }, [snapshot, inFlight]);

  useEffect(() => {
    if (!snapshot) return;
    for (const entry of ledger) {
      if (
        entry.status !== "running" ||
        !entry.commandSessionId ||
        inFlight.has(entry.commandSessionId) ||
        finalizingRef.current.has(entry.commandSessionId)
      ) {
        continue;
      }
      const commandSessionId = entry.commandSessionId;
      finalizingRef.current.add(commandSessionId);
      void rpc<CommandOutput>("read_command_lines", sandboxScope(sandboxId), {
        command_session_id: commandSessionId,
        start_offset: 0,
        limit: 1,
      })
        .then((output) => {
          if (output.status !== "running") {
            patchEntry(entry.localId, {
              status: output.status,
              exitCode: output.exit_code,
              endedAt: Date.now(),
              publishRejected: output.publish_rejected === true,
              publishRejectClass: output.publish_reject_class ?? null,
            });
          }
        })
        .catch(() => {})
        .finally(() => finalizingRef.current.delete(commandSessionId));
    }
  }, [snapshot, ledger, inFlight, sandboxId]);

  const patchEntry = useCallback((localId: string, patch: Partial<LedgerEntry>) => {
    setLedger((current) =>
      current.map((entry) =>
        entry.localId === localId ? { ...entry, ...patch } : entry,
      ),
    );
  }, []);

  useEffect(() => {
    const hash = location.hash;
    if (!hash.startsWith("#cmd-")) return;
    const commandSessionId = decodeURIComponent(hash.slice(5));
    const entry = ledger.find((item) => item.commandSessionId === commandSessionId);
    if (!entry) return;
    setExpanded((current) => new Set(current).add(entry.localId));
    requestAnimationFrame(() => {
      document
        .getElementById(`cmd-${commandSessionId}`)
        ?.scrollIntoView({ block: "center" });
    });
  }, [location.hash, ledger]);

  const onLaunched = useCallback(
    (cmd: string, workspaceSessionId: string | null, output: CommandOutput) => {
      const entry = entryFromExec(cmd, workspaceSessionId, output);
      setLedger((current) => [...current, entry]);
      setExpanded((current) => new Set(current).add(entry.localId));
      void queryClient.invalidateQueries({ queryKey: ["sandbox", sandboxId, "snapshot"] });
      requestAnimationFrame(() => {
        const pane = ledgerPaneRef.current;
        if (pane) pane.scrollTop = pane.scrollHeight;
        if (entry.commandSessionId) {
          const frame = document.getElementById(`cmd-${entry.commandSessionId}`);
          (frame?.querySelector("[tabindex]") as HTMLElement | null)?.focus();
        }
      });
    },
    [queryClient, sandboxId],
  );

  const visibleLedger = mode === "session"
    ? ledger.filter((entry) => entry.workspaceSessionId === selectedSession)
    : mode === "quick"
      ? ledger.filter((entry) => entry.autoPublish)
      : ledger;

  const historyLabel = mode === "session"
    ? selectedSession
    : mode === "quick"
      ? "quick run · shared · auto-publish"
      : "all commands";

  const scopes = previewScopes(snapshot ?? undefined);

  return (
    <TranscriptPollProvider>
      <Flex data-terminal-workspace h="100%" mih={0} miw={0} style={{ flex: 1, overflow: "hidden" }}>
        <SessionSidebar
          sandboxId={sandboxId}
          workspaces={workspaces}
          mode={mode}
          selected={selectedSession}
          narrow={narrow}
          opened={sessionsOpen}
          onClose={() => setSessionsOpen(false)}
          onSelect={(nextMode, sessionId) => {
            const next = new URLSearchParams(searchParams);
            if (nextMode === "session" && sessionId) {
              next.set("session", sessionId);
              next.delete("view");
            } else {
              next.delete("session");
              if (nextMode === "all") next.set("view", "all");
              else next.delete("view");
            }
            setSearchParams(next, { replace: true });
            setSessionsOpen(false);
          }}
        />
        <Flex direction="column" mih={0} miw={0} style={{ flex: 1 }}>
        <Paper component="header" data-terminal-toolbar px="md" py="sm" radius={0} withBorder>
          <Group justify="space-between" wrap="wrap">
            <Group gap="sm" wrap="nowrap">
              {narrow ? (
                <Button
                  aria-label="Open sessions"
                  leftSection={<PanelLeft size={14} />}
                  onClick={() => setSessionsOpen(true)}
                >
                  Sessions
                </Button>
              ) : null}
              <Box>
                <Title order={2} size="sm">Terminal ledger</Title>
                <Text c="dimmed" size="xs">
                  history: {historyLabel}
                </Text>
              </Box>
            </Group>
            <Text c="dimmed" size="xs">
              {visibleLedger.length} {visibleLedger.length === 1 ? "command" : "commands"}
            </Text>
          </Group>
        </Paper>
        <Box ref={ledgerPaneRef} data-terminal-ledger p="md" style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {visibleLedger.length === 0 ? (
            <Center h="100%">
              <Paper maw={480} p="xl" ta="center" withBorder>
                <Title order={3} size="sm">No commands yet</Title>
                <Text c="dimmed" mt="xs" size="xs">
                  {mode === "session"
                    ? `Nothing has run in ${selectedSession} from this browser.`
                    : mode === "quick"
                      ? "Run the first one-off command below — each command opens its own terminal."
                      : "No commands have been recorded in this browser."}
                </Text>
              </Paper>
            </Center>
          ) : (
            <Stack gap="sm">
              {visibleLedger.map((entry) => (
                <CommandCard
                  key={entry.localId}
                  sandboxId={sandboxId}
                  entry={entry}
                  expanded={expanded.has(entry.localId)}
                  onToggle={() =>
                    setExpanded((current) => {
                      const next = new Set(current);
                      if (next.has(entry.localId)) next.delete(entry.localId);
                      else next.add(entry.localId);
                      return next;
                    })
                  }
                  onUpdate={(patch) => patchEntry(entry.localId, patch)}
                  previewScopes={scopes}
                />
              ))}
            </Stack>
          )}
        </Box>
        {mode === "all" ? (
          <Paper data-terminal-context-prompt px="md" py="sm" radius={0} ta="center" withBorder>
            <Text c="dimmed" size="sm">
              Choose a workspace session or Quick run to run a command.
            </Text>
          </Paper>
        ) : (
          <CommandComposer
            sandboxId={sandboxId}
            workspaceSessionId={mode === "session" ? selectedSession : null}
            workspace={selectedWorkspace}
            onLaunched={onLaunched}
          />
        )}
        </Flex>
      </Flex>
    </TranscriptPollProvider>
  );
}
