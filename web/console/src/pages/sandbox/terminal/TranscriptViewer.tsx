import { Alert, Box, Text } from "@mantine/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput } from "@/api/types";
import { absorbOutput, transcriptFor } from "@/pages/sandbox/terminal/ledger";
import { useTranscriptPoller } from "@/pages/sandbox/terminal/TranscriptPollProvider";

const PAGE_LIMIT = 1000;

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
  onTerminal: (output: CommandOutput) => void;
  registerNudge?: (nudge: () => void) => void;
}) {
  const [, setVersion] = useState(0);
  const stateRef = useRef(transcriptFor(commandSessionId, sandboxId));
  const fetchingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollSettleTimerRef = useRef<number | null>(null);

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
          onTerminal(output);
        }
        const more = state.fetchedTo < state.totalLines;
        if (!more) break;
      }
      stateRef.current.error = null;
    } catch (error) {
      // Poll errors keep the last good transcript; the coordinator retries.
      stateRef.current.error =
        error instanceof Error ? error.message : "Transcript refresh failed.";
      setVersion((version) => version + 1);
    } finally {
      fetchingRef.current = false;
    }
  }, [sandboxId, commandSessionId, onTerminal]);

  useEffect(() => {
    void fetchPages();
  }, [fetchPages]);

  useTranscriptPoller(`${sandboxId}:${commandSessionId}`, running, fetchPages);

  useEffect(() => {
    registerNudge?.(() => void fetchPages());
  }, [registerNudge, fetchPages]);

  useEffect(
    () => () => {
      if (scrollSettleTimerRef.current !== null) {
        window.clearTimeout(scrollSettleTimerRef.current);
      }
    },
    [],
  );

  const state = stateRef.current;
  const count = state.lines.length;

  const virtualizer = useVirtualizer({
    count,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 18,
    measureElement: (element) => element?.getBoundingClientRect().height ?? 18,
    overscan: 30,
  });

  useEffect(() => {
    if (state.tailPinned !== false && count > 0) {
      virtualizer.scrollToIndex(count - 1, { align: "end" });
    }
  }, [count, state.tailPinned, virtualizer]);

  const onScroll = () => {
    const pane = scrollRef.current;
    if (!pane) return;
    // TanStack Virtual scrolls while it reconciles variable-height rows.
    // Settle before treating a position as user intent; transient programmatic
    // positions during a paged catch-up must not disable tail following.
    if (scrollSettleTimerRef.current !== null) {
      window.clearTimeout(scrollSettleTimerRef.current);
    }
    scrollSettleTimerRef.current = window.setTimeout(() => {
      const current = scrollRef.current;
      if (!current) return;
      const tailPinned =
        current.scrollHeight - current.scrollTop - current.clientHeight < 40;
      if (stateRef.current.tailPinned !== tailPinned) {
        stateRef.current.tailPinned = tailPinned;
        setVersion((version) => version + 1);
      }
    }, 48);
  };

  return (
    <Box
      ref={scrollRef}
      onScroll={onScroll}
      data-terminal-transcript
      data-transcript-scroll-owner
      p="sm"
      style={{
        height: "16rem",
        overflowY: "auto",
        fontFamily: "var(--mantine-font-family-monospace)",
        fontSize: "var(--mantine-font-size-xs)",
        lineHeight: "18px",
      }}
    >
      {state.error ? (
        <Alert data-terminal-transcript-stale color="yellow" mb="xs" title="Transcript refresh delayed">
          Showing the last fetched output while the connection recovers.
        </Alert>
      ) : null}
      <Box
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: "100%",
          position: "relative",
        }}
      >
        {virtualizer.getVirtualItems().map((item) => (
          <Text
            key={item.key}
            data-terminal-line
            data-index={item.index}
            ref={virtualizer.measureElement}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${item.start}px)`,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
            }}
          >
            {state.lines[item.index] ?? ""}
          </Text>
        ))}
      </Box>
      {count === 0 ? (
        <Text c="dimmed" size="xs">
          {running ? "waiting for output…" : "no output"}
        </Text>
      ) : null}
      <Box style={{ position: "sticky", bottom: 0, display: "flex", justifyContent: "flex-end" }}>
        <Text
          c="dimmed"
          size="10px"
          px={4}
          style={{ background: "var(--mantine-color-body)", opacity: 0.9 }}
        >
          lines {state.fetchedTo} of {state.totalLines}
          {running ? " · tailing" : ""}
        </Text>
      </Box>
    </Box>
  );
}
