import { useState } from "react";
import { useSearchParams } from "react-router";
import { fetchEvents } from "@/api/observability";
import { usePoll } from "@/poll/usePoll";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { TraceCell } from "@/pages/sandbox/observability/TracesView";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/cn";
import { formatTimestamp } from "@/lib/format";

const SINCE_CHOICES = [
  { label: "any time", ms: 0 },
  { label: "last 5m", ms: 5 * 60_000 },
  { label: "last 15m", ms: 15 * 60_000 },
  { label: "last 1h", ms: 60 * 60_000 },
];

/**
 * EventStream: newest-first table over the `events` view with the API's
 * exact filters (name, since-ms, last-N) plus a polling live-tail toggle.
 * Trace cells deep-link into the waterfall.
 */
export function EventsView() {
  const { sandboxId } = useSandbox();
  const [searchParams, setSearchParams] = useSearchParams();
  const name = searchParams.get("name") ?? "";
  const sinceMs = Number(searchParams.get("since") ?? 0);
  const lastN = Number(searchParams.get("last") ?? 200);
  const [tail, setTail] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  const events = usePoll({
    key: ["observability", sandboxId, "events", name, sinceMs, lastN],
    fn: () =>
      fetchEvents(sandboxId, {
        name: name || undefined,
        sinceMs: sinceMs > 0 ? sinceMs : undefined,
        lastN,
      }),
    mode: tail ? "fast" : "slow",
    enabled: tail || true,
  });

  const apply = (next: { name?: string; since?: number; last?: number }) => {
    const params = new URLSearchParams(searchParams);
    if (next.name !== undefined) {
      if (next.name) params.set("name", next.name);
      else params.delete("name");
    }
    if (next.since !== undefined) {
      if (next.since > 0) params.set("since", String(next.since));
      else params.delete("since");
    }
    if (next.last !== undefined) params.set("last", String(next.last));
    setSearchParams(params, { replace: true });
  };

  const rows = [...(events.data?.events ?? [])].sort((a, b) => b.ts - a.ts);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2">
        <label className="text-[11px] text-ink-faint" htmlFor="event-name">
          name
        </label>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            apply({ name: (event.currentTarget.elements.namedItem("name") as HTMLInputElement).value });
          }}
        >
          <Input
            id="event-name"
            name="name"
            defaultValue={name}
            placeholder="lease.acquired"
            className="w-52 font-mono"
          />
        </form>
        <label className="text-[11px] text-ink-faint">since</label>
        <Select
          value={String(sinceMs)}
          onValueChange={(value) => apply({ since: Number(value) })}
        >
          <SelectTrigger className="w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SINCE_CHOICES.map((choice) => (
              <SelectItem key={choice.ms} value={String(choice.ms)}>
                {choice.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <label className="text-[11px] text-ink-faint" htmlFor="event-last">
          last-N
        </label>
        <Input
          id="event-last"
          value={String(lastN)}
          onChange={(event) => {
            const parsed = Number(event.target.value);
            if (Number.isFinite(parsed) && parsed > 0) apply({ last: parsed });
          }}
          className="w-20 font-mono"
          inputMode="numeric"
        />
        <button
          type="button"
          onClick={() => setTail((current) => !current)}
          className={cn(
            "ml-auto flex items-center gap-1.5 rounded border px-2 py-1 text-[11px]",
            tail
              ? "border-accent bg-accent-soft text-accent"
              : "border-line text-ink-mid hover:bg-surface-hover",
          )}
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              tail ? "animate-pulse bg-accent" : "bg-idle",
            )}
          />
          tail
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.length === 0 ? (
          <div className="mx-auto mt-16 max-w-md rounded-lg border border-line bg-surface p-8 text-center">
            <div className="text-sm font-semibold">No events</div>
            <p className="mt-2 text-xs text-ink-mid">
              Nothing matches these filters yet. Domain facts (leases,
              publishes) appear here as they happen.
            </p>
          </div>
        ) : (
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr className="border-b border-line text-left text-[11px] text-ink-faint">
                <th className="px-4 py-1.5 font-medium">ts</th>
                <th className="px-2 py-1.5 font-medium">name</th>
                <th className="px-2 py-1.5 font-medium">trace</th>
                <th className="px-2 py-1.5 font-medium">attrs</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((event) => {
                const key = `${event.trace}-${event.ts}-${event.name}`;
                const attrsText = JSON.stringify(event.attrs);
                const isOpen = expanded === key;
                return (
                  <tr
                    key={key}
                    className="h-8 border-b border-line/60 align-top hover:bg-surface-hover"
                  >
                    <td className="whitespace-nowrap px-4 py-1.5 font-mono text-ink-mid">
                      {formatTimestamp(event.ts)}
                    </td>
                    <td className="px-2 py-1.5 font-mono">{event.name}</td>
                    <td className="px-2 py-1.5">
                      <TraceCell sandboxId={sandboxId} traceId={event.trace} />
                    </td>
                    <td className="max-w-96 px-2 py-1.5">
                      <button
                        type="button"
                        onClick={() => setExpanded(isOpen ? null : key)}
                        className={cn(
                          "block w-full break-all text-left font-mono text-[11px] text-ink-mid hover:text-ink",
                          !isOpen && "truncate",
                        )}
                        title="toggle full attrs"
                      >
                        {attrsText}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
