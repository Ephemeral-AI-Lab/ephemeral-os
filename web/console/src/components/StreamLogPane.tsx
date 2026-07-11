import { useEffect, useRef } from "react";
import { Box, Text } from "@mantine/core";

/**
 * Renders `_stream_logs` progress lines (streamed over SSE) as a
 * tail-pinned mono pane; used by create/destroy/squash.
 */
export function StreamLogPane({
  lines,
  maxHeight = 160,
}: {
  lines: string[];
  maxHeight?: number | string;
}) {
  const paneRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const pane = paneRef.current;
    if (pane) pane.scrollTop = pane.scrollHeight;
  }, [lines.length]);

  return (
    <Box
      ref={paneRef}
      style={{
        maxHeight,
        overflowY: "auto",
        border: "1px solid var(--mantine-color-neutral-3)",
        borderRadius: "var(--mantine-radius-sm)",
        background: "var(--mantine-color-warm-0)",
        padding: "var(--mantine-spacing-sm)",
        color: "var(--mantine-color-dimmed)",
        fontFamily: "var(--mantine-font-family-monospace)",
        fontSize: 11,
        lineHeight: 1.625,
      }}
    >
      {lines.length === 0 ? (
        <Text component="span" fz="inherit" c="dimmed">waiting for progress…</Text>
      ) : (
        lines.map((line, index) => (
          <Box key={index} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {line}
          </Box>
        ))
      )}
    </Box>
  );
}
