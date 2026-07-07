import { useEffect, useRef } from "react";
import { cn } from "@/lib/cn";

/**
 * Renders `_stream_logs` progress lines (streamed over SSE) as a
 * tail-pinned mono pane; used by create/destroy/squash.
 */
export function StreamLogPane({
  lines,
  className,
  maxHeightClass = "max-h-40",
}: {
  lines: string[];
  className?: string;
  maxHeightClass?: string;
}) {
  const paneRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const pane = paneRef.current;
    if (pane) pane.scrollTop = pane.scrollHeight;
  }, [lines.length]);

  return (
    <div
      ref={paneRef}
      className={cn(
        "overflow-y-auto rounded border border-line bg-app p-2 font-mono text-[11px] leading-relaxed text-ink-mid",
        maxHeightClass,
        className,
      )}
    >
      {lines.length === 0 ? (
        <span className="text-ink-faint">waiting for progress…</span>
      ) : (
        lines.map((line, index) => (
          <div key={index} className="whitespace-pre-wrap break-all">
            {line}
          </div>
        ))
      )}
    </div>
  );
}
