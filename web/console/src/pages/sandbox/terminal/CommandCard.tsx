import { useCallback, useRef } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
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
    if (!running || !entry.commandSessionId) return;
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed) return;
    if (event.ctrlKey && (event.key === "c" || event.key === "d")) {
      event.preventDefault();
      void rpc("write_command_stdin", sandboxScope(sandboxId), {
        command_session_id: entry.commandSessionId,
        stdin: event.key === "c" ? CTRL_C : CTRL_D,
        yield_time_ms: 0,
      }).finally(() => nudgeRef.current());
    }
  };

  return (
    <div
      id={entry.commandSessionId ? `cmd-${entry.commandSessionId}` : undefined}
      className={`rounded-md border ${expanded ? "border-accent/50" : "border-line"} bg-surface`}
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex h-8 w-full items-center gap-2 px-2 text-left hover:bg-surface-hover"
      >
        {expanded ? (
          <ChevronDown size={13} className="shrink-0 text-ink-faint" />
        ) : (
          <ChevronRight size={13} className="shrink-0 text-ink-faint" />
        )}
        <span className="font-mono text-xs text-ink-faint">$</span>
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{entry.cmd}</span>
        <span
          className="shrink-0 rounded bg-idle-soft px-1 py-px font-mono text-[10px] text-ink-mid"
          title="owning workspace session"
        >
          {entry.autoPublish
            ? "auto-publish"
            : (entry.workspaceSessionId ?? "session ?")}
        </span>
        {running ? (
          <span className="flex shrink-0 items-center gap-1 text-[11px] text-run">
            <Loader2 size={11} className="animate-spin" />
            {formatDuration(elapsedSeconds)}
          </span>
        ) : (
          <span className="flex shrink-0 items-center gap-1">
            {entry.publishRejected ? (
              <StateBadge
                state="danger"
                label={`publish rejected${entry.publishRejectClass ? ` · ${entry.publishRejectClass}` : ""}`}
              />
            ) : (
              <StateBadge
                state={entry.status}
                label={
                  entry.exitCode !== null
                    ? `${entry.status} · exit ${entry.exitCode}`
                    : entry.status
                }
              />
            )}
          </span>
        )}
      </button>

      {expanded ? (
        <div
          className="border-t border-line focus:outline-none"
          tabIndex={0}
          onKeyDown={frameKeyDown}
        >
          {running ? (
            <div className="flex items-center justify-end gap-2 border-b border-line bg-app/60 px-2 py-1">
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
            </div>
          ) : null}
          {entry.commandSessionId ? (
            <TranscriptViewer
              sandboxId={sandboxId}
              commandSessionId={entry.commandSessionId}
              running={running}
              onTerminal={onTerminal}
              registerNudge={registerNudge}
            />
          ) : (
            <InlineTranscript output={entry.inlineOutput ?? ""} />
          )}
          {running && entry.commandSessionId ? (
            <StdinBar
              sandboxId={sandboxId}
              commandSessionId={entry.commandSessionId}
              nudge={() => nudgeRef.current()}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The inline path: a command that beat exec_command's initial wait returns
 * its transcript inline with no command_session_id — render it directly,
 * nothing to poll.
 */
function InlineTranscript({ output }: { output: string }) {
  return (
    <div className="max-h-64 overflow-y-auto bg-app px-2 py-1 font-mono text-xs leading-[18px]">
      {output.length > 0 ? (
        output.split("\n").map((line, index) => (
          <div key={index} className="whitespace-pre-wrap break-all">
            {line}
          </div>
        ))
      ) : (
        <span className="text-ink-faint">no output</span>
      )}
    </div>
  );
}
