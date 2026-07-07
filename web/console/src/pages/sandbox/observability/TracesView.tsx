import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ChevronDown, ChevronRight, Flag } from "lucide-react";
import {
  fetchEvents,
  fetchTrace,
  type TraceNode,
} from "@/api/observability";
import { usePoll } from "@/poll/usePoll";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { cn } from "@/lib/cn";
import { formatTimestamp } from "@/lib/format";

const STATUS_COLORS: Record<string, string> = {
  completed: "bg-ok",
  error: "bg-danger",
  cancelled: "bg-idle",
  timed_out: "bg-warn",
};

/**
 * Traces: a list (the `last` selector plus trace ids discovered from recent
 * events — no trace-enumeration op exists) and the waterfall of the selected
 * trace with nested bars, status colors, pinned ⚑ events, and per-span attrs.
 * `traces/:traceId` is the deep-link target of event rows and blame owners.
 */
export function TracesView() {
  const { sandboxId } = useSandbox();
  const params = useParams();
  const navigate = useNavigate();
  const selected = params.traceId ?? "last";

  const recentEvents = usePoll({
    key: ["observability", sandboxId, "events", "for-traces"],
    fn: () => fetchEvents(sandboxId, { lastN: 200 }),
    mode: "slow",
  });

  const trace = usePoll({
    key: ["observability", sandboxId, "trace", selected],
    fn: () => fetchTrace(sandboxId, selected),
    mode: "slow",
    retry: false,
  });

  const knownTraces = useMemo(() => {
    const ordered: { id: string; ts: number }[] = [];
    const seen = new Set<string>();
    for (const event of recentEvents.data?.events ?? []) {
      if (!seen.has(event.trace)) {
        seen.add(event.trace);
        ordered.push({ id: event.trace, ts: event.ts });
      }
    }
    ordered.sort((a, b) => b.ts - a.ts);
    return ordered;
  }, [recentEvents.data]);

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-72 shrink-0 flex-col border-r border-line bg-surface">
        <div className="border-b border-line px-3 py-2 text-xs font-semibold uppercase tracking-wide text-ink-mid">
          Traces
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          <TraceRow
            id="last"
            label="last trace"
            hint="the most recent flow"
            active={selected === "last"}
            onClick={() => void navigate("../traces")}
          />
          {knownTraces.map((known) => (
            <TraceRow
              key={known.id}
              id={known.id}
              label={known.id}
              hint={formatTimestamp(known.ts)}
              active={selected === known.id}
              onClick={() => void navigate(`../traces/${encodeURIComponent(known.id)}`)}
            />
          ))}
          {knownTraces.length === 0 ? (
            <p className="px-2 py-3 text-[11px] text-ink-faint">
              No traces discovered from recent events yet — deep links and
              the last selector still resolve.
            </p>
          ) : null}
        </div>
      </aside>

      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4">
        {trace.isError ? (
          <div className="rounded border border-danger/40 bg-danger-soft p-3 text-xs">
            {(trace.error as Error).message}
          </div>
        ) : trace.data ? (
          <Waterfall traceId={trace.data.trace} roots={trace.data.spans} />
        ) : (
          <div className="animate-pulse text-xs text-ink-faint">loading trace…</div>
        )}
      </div>
    </div>
  );
}

function TraceRow({
  id,
  label,
  hint,
  active,
  onClick,
}: {
  id: string;
  label: string;
  hint: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "mb-0.5 block w-full rounded px-2 py-1.5 text-left",
        active ? "bg-accent-soft" : "hover:bg-surface-hover",
      )}
      title={id}
    >
      <span
        className={cn(
          "block truncate font-mono text-xs",
          active ? "font-medium text-accent" : "text-ink",
        )}
      >
        {label}
      </span>
      <span className="block text-[10px] text-ink-faint">{hint}</span>
    </button>
  );
}

interface FlatSpan {
  node: TraceNode;
  depth: number;
}

function flatten(nodes: TraceNode[], depth: number): FlatSpan[] {
  return nodes.flatMap((node) => [
    { node, depth },
    ...flatten(node.children, depth + 1),
  ]);
}

function Waterfall({ traceId, roots }: { traceId: string; roots: TraceNode[] }) {
  const rows = useMemo(() => flatten(roots, 0), [roots]);
  const totalMs = useMemo(
    () =>
      Math.max(
        1,
        ...rows.map((row) => row.node.offset_ms + (row.node.span.dur_ms ?? 0)),
      ),
    [rows],
  );

  if (rows.length === 0) {
    return (
      <p className="text-xs text-ink-faint">
        Trace <span className="font-mono">{traceId}</span> has no spans.
      </p>
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-baseline gap-3">
        <h3 className="font-mono text-sm font-semibold">{traceId}</h3>
        <span className="text-xs text-ink-mid">total {totalMs.toFixed(0)}ms</span>
      </div>
      <div className="flex flex-col">
        {rows.map((row) => (
          <SpanRow
            key={row.node.span.span}
            row={row}
            totalMs={totalMs}
          />
        ))}
      </div>
    </div>
  );
}

function SpanRow({ row, totalMs }: { row: FlatSpan; totalMs: number }) {
  const [open, setOpen] = useState(false);
  const span = row.node.span;
  const duration = span.dur_ms ?? 0;
  const left = (row.node.offset_ms / totalMs) * 100;
  const width = Math.max((duration / totalMs) * 100, 0.5);
  const attrs = Object.entries(span.attrs);

  return (
    <div className="border-b border-line/60 py-0.5">
      <div className="flex h-6 items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          className="flex w-64 shrink-0 items-center gap-1 truncate text-left hover:text-accent"
          style={{ paddingLeft: `${row.depth * 14}px` }}
          title={`${span.name} attrs`}
        >
          {attrs.length > 0 ? (
            open ? (
              <ChevronDown size={11} className="shrink-0 text-ink-faint" />
            ) : (
              <ChevronRight size={11} className="shrink-0 text-ink-faint" />
            )
          ) : (
            <span className="w-[11px] shrink-0" />
          )}
          <span className="truncate font-mono text-xs">{span.name}</span>
        </button>
        <div className="relative h-4 min-w-0 flex-1 rounded-sm bg-app">
          <div
            className={cn(
              "absolute top-0 h-4 rounded-sm",
              STATUS_COLORS[span.status] ?? "bg-idle",
            )}
            style={{ left: `${left}%`, width: `${width}%`, opacity: 0.75 }}
            title={`${span.name} · ${duration.toFixed(1)}ms · ${span.status}`}
          />
          {row.node.events.map((event) => (
            <span
              key={`${event.name}-${event.ts}`}
              className="absolute -top-0.5 text-warn"
              style={{
                left: `${Math.min(((event.ts - (span.ts - row.node.offset_ms)) / totalMs) * 100, 99)}%`,
              }}
              title={`⚑ ${event.name}`}
            >
              <Flag size={10} />
            </span>
          ))}
        </div>
        <span className="w-20 shrink-0 text-right font-mono text-[11px] text-ink-mid">
          {duration.toFixed(1)}ms
        </span>
        <span className="w-20 shrink-0 text-right text-[11px] text-ink-faint">
          {span.status}
        </span>
      </div>
      {open && attrs.length > 0 ? (
        <dl
          className="mb-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded border border-line bg-app px-2 py-1.5 text-[11px]"
          style={{ marginLeft: `${row.depth * 14 + 16}px` }}
        >
          {attrs.map(([key, value]) => (
            <SpanAttr key={key} name={key} value={value} />
          ))}
        </dl>
      ) : null}
    </div>
  );
}

function SpanAttr({ name, value }: { name: string; value: unknown }) {
  return (
    <>
      <dt className="font-mono text-ink-faint">{name}</dt>
      <dd className="break-all font-mono text-ink">
        {typeof value === "string" ? value : JSON.stringify(value)}
      </dd>
    </>
  );
}

export function traceLink(sandboxId: string, traceId: string): string {
  return `/sandboxes/${encodeURIComponent(sandboxId)}/observability/traces/${encodeURIComponent(traceId)}`;
}

export function TraceCell({ sandboxId, traceId }: { sandboxId: string; traceId: string }) {
  return (
    <Link
      to={traceLink(sandboxId, traceId)}
      className="font-mono text-accent hover:underline"
      title={traceId}
    >
      {traceId.length > 12 ? `${traceId.slice(0, 12)}…` : traceId}
    </Link>
  );
}
