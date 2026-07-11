import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput } from "@/api/types";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { previewScopes } from "@/pages/sandbox/SandboxHeader";
import { CommandCard } from "@/pages/sandbox/terminal/CommandCard";
import { CommandComposer } from "@/pages/sandbox/terminal/CommandComposer";
import { SessionSidebar } from "@/pages/sandbox/terminal/SessionSidebar";
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
  const [ledger, setLedger] = useState<LedgerEntry[]>(() => loadLedger(sandboxId));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const ledgerPaneRef = useRef<HTMLDivElement>(null);
  const finalizingRef = useRef<Set<string>>(new Set());

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

  const inFlight = useMemo(() => {
    const map = new Map<string, string>();
    for (const workspace of workspaces) {
      for (const execution of workspace.active_namespace_executions) {
        map.set(execution.namespace_execution_id, workspace.workspace_id);
      }
    }
    return map;
  }, [workspaces]);

  useEffect(() => {
    if (!snapshot) return;
    setLedger((current) => {
      const known = new Set(
        current
          .map((entry) => entry.commandSessionId)
          .filter((id): id is string => id !== null),
      );
      const additions: LedgerEntry[] = [];
      for (const [commandSessionId, workspaceId] of inFlight) {
        if (!known.has(commandSessionId)) {
          additions.push(entryFromSnapshot(commandSessionId, workspaceId));
        }
      }
      return additions.length > 0 ? [...current, ...additions] : current;
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

  const visibleLedger = selectedSession
    ? ledger.filter((entry) => entry.workspaceSessionId === selectedSession)
    : ledger;

  const scopes = previewScopes(snapshot ?? undefined);

  return (
    <div className="flex h-full min-h-0">
      <SessionSidebar
        workspaces={workspaces}
        selected={selectedSession}
        onSelect={(sessionId) => {
          const next = new URLSearchParams(searchParams);
          if (sessionId) next.set("session", sessionId);
          else next.delete("session");
          setSearchParams(next, { replace: true });
        }}
      />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div ref={ledgerPaneRef} className="min-h-0 flex-1 overflow-y-auto p-3">
          {visibleLedger.length === 0 ? (
            <div className="mx-auto mt-16 max-w-md rounded-lg border border-line bg-surface p-8 text-center">
              <div className="text-sm font-semibold">No commands yet</div>
              <p className="mt-2 text-xs text-ink-mid">
                {selectedSession
                  ? `Nothing has run in ${selectedSession} from this browser.`
                  : "Run the first command below — each command opens its own terminal."}
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
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
            </div>
          )}
        </div>
        <CommandComposer
          sandboxId={sandboxId}
          workspaces={workspaces}
          targetSession={selectedSession}
          onLaunched={onLaunched}
        />
      </div>
    </div>
  );
}
