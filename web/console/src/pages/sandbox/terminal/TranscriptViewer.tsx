import { useCallback, useEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput, CommandStatus } from "@/api/types";
import { absorbOutput, transcriptFor } from "@/pages/sandbox/terminal/ledger";

const PAGE_LIMIT = 1000;
const FAST_TAIL_MS = 400;
const SETTLED_TAIL_MS = 2000;

export interface TranscriptHandle {
  nudge: () => void;
}

/**
 * Offset-tracked transcript tail over read_command_lines (≤1000 lines per
 * fetch, stable offsets). Catches up from the last fetched offset in a
 * burst of pages, then tails; scroll position pins to the tail unless the
 * user scrolls up. Line discipline only — no PTY emulation.
 */
export function TranscriptViewer({
  sandboxId,
  commandSessionId,
  running,
  onTerminal,
  registerNudge,
}: {
  sandboxId: string;
  commandSessionId: string;
  running: boolean;
  onTerminal: (status: CommandStatus, exitCode: number | null) => void;
  registerNudge?: (nudge: () => void) => void;
}) {
  const [, setVersion] = useState(0);
  const stateRef = useRef(transcriptFor(commandSessionId));
  const pinnedRef = useRef(true);
  const fetchingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const runningRef = useRef(running);
  runningRef.current = running;

  const fetchPages = useCallback(async () => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      for (;;) {
        const state = stateRef.current;
        const output = await rpc<CommandOutput>(
          "read_command_lines",
          sandboxScope(sandboxId),
          {
            command_session_id: commandSessionId,
            start_offset: state.fetchedTo,
            limit: PAGE_LIMIT,
          },
        );
        absorbOutput(state, output);
        setVersion((version) => version + 1);
        if (output.status !== "running") {
          onTerminal(output.status, output.exit_code);
        }
        const more = state.fetchedTo < state.totalLines;
        if (!more) break;
      }
    } catch {
      // Poll errors keep the last good transcript; the next tick retries.
    } finally {
      fetchingRef.current = false;
    }
  }, [sandboxId, commandSessionId, onTerminal]);

  useEffect(() => {
    void fetchPages();
    if (!running) return;
    const interval = setInterval(
      () => void fetchPages(),
      document.hidden ? SETTLED_TAIL_MS : FAST_TAIL_MS,
    );
    return () => clearInterval(interval);
  }, [fetchPages, running]);

  useEffect(() => {
    registerNudge?.(() => void fetchPages());
  }, [registerNudge, fetchPages]);

  const state = stateRef.current;
  const count = state.lines.length;

  const virtualizer = useVirtualizer({
    count,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 18,
    overscan: 30,
  });

  useEffect(() => {
    if (pinnedRef.current && count > 0) {
      virtualizer.scrollToIndex(count - 1, { align: "end" });
    }
  }, [count, virtualizer]);

  const onScroll = () => {
    const pane = scrollRef.current;
    if (!pane) return;
    pinnedRef.current =
      pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
  };

  return (
    <div
      ref={scrollRef}
      onScroll={onScroll}
      className="h-64 overflow-y-auto bg-app px-2 py-1 font-mono text-xs leading-[18px] text-ink"
    >
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: "100%",
          position: "relative",
        }}
      >
        {virtualizer.getVirtualItems().map((item) => (
          <div
            key={item.key}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${item.start}px)`,
            }}
            className="whitespace-pre-wrap break-all"
          >
            {state.lines[item.index] ?? ""}
          </div>
        ))}
      </div>
      {count === 0 ? (
        <div className="text-ink-faint">
          {running ? "waiting for output…" : "no output"}
        </div>
      ) : null}
      <div className="sticky bottom-0 flex justify-end">
        <span className="rounded-tl bg-surface/90 px-1 text-[10px] text-ink-faint">
          lines {state.fetchedTo} of {state.totalLines}
          {running ? " · tailing" : ""}
        </span>
      </div>
    </div>
  );
}
