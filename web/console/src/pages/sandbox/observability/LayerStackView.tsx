import { useMemo } from "react";
import { fetchLayerStack, type StackLayer } from "@/api/observability";
import { usePoll } from "@/poll/usePoll";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { SquashDialog } from "@/components/SquashDialog";
import { ResourceSparkline } from "@/components/ResourceSparkline";
import { Button } from "@/components/ui/button";
import { DialogTrigger } from "@/components/ui/dialog";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { formatBytes, shortHash } from "@/lib/format";

const depthRings = new Map<string, number[]>();

function recordDepth(sandboxId: string, depth: number): number[] {
  let ring = depthRings.get(sandboxId);
  if (!ring) {
    ring = [];
    depthRings.set(sandboxId, ring);
  }
  if (ring.length === 0 || ring[ring.length - 1] !== depth) {
    ring.push(depth);
    if (ring.length > 24) ring.shift();
  }
  return [...ring];
}

/**
 * LayerStackViz: the stack as a vertical column (newest on top), disk bytes
 * and lease/booking counts per layer, squashable runs bracketed, a depth
 * trend accumulated client-side, and the SquashButton. A pre-run "est.
 * after" count is not derivable from any op — the header shows the current
 * count and the post-squash count arrives from the refetch.
 */
export function LayerStackView() {
  const { sandboxId } = useSandbox();
  const stack = usePoll({
    key: ["observability", sandboxId, "layerstack"],
    fn: () => fetchLayerStack(sandboxId),
    mode: "slow",
  });

  const layers = useMemo(() => stack.data?.layers ?? [], [stack.data]);
  const squashableRuns = useMemo(() => squashRuns(layers), [layers]);
  const depthTrend = stack.data ? recordDepth(sandboxId, layers.length) : [];
  const maxBytes = Math.max(1, ...layers.map((layer) => layer.bytes));

  if (stack.isError) {
    return (
      <div className="m-4 rounded border border-danger/40 bg-danger-soft p-3 text-xs">
        {(stack.error as Error).message}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-mid">
        <span>
          manifest v<span className="font-mono text-ink">{stack.data?.manifest_version ?? "…"}</span>
        </span>
        <span className="font-mono" title="root hash">
          {stack.data ? shortHash(stack.data.root_hash, 12) : "…"}
        </span>
        <span>
          {layers.length} layers · {formatBytes(stack.data?.total_bytes ?? 0)}
        </span>
        <span>{stack.data?.active_lease_count ?? 0} active leases</span>
        <span className="ml-auto">
          <SquashDialog
            sandboxId={sandboxId}
            layerCount={layers.length}
            trigger={
              <DialogTrigger asChild>
                <Button size="sm" variant="primary">
                  Squash ({layers.length})
                </Button>
              </DialogTrigger>
            }
          />
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_260px]">
        <section className="rounded-lg border border-line bg-surface p-3">
          {layers.length === 0 ? (
            <p className="text-xs text-ink-faint">no layers yet</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {layers.map((layer, index) => (
                <LayerRow
                  key={layer.layer_id}
                  layer={layer}
                  index={index}
                  total={layers.length}
                  maxBytes={maxBytes}
                  squashable={squashableRuns.has(index)}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-3">
          <div className="rounded-lg border border-line bg-surface p-3">
            <h3 className="mb-2 text-xs font-semibold text-ink-mid">
              stack depth (this session)
            </h3>
            <ResourceSparkline values={depthTrend} width={220} height={36} label="stack depth" />
            <p className="mt-1 text-[11px] text-ink-faint">
              {layers.length} now · trend accumulates while this view polls
            </p>
          </div>
          <div className="rounded-lg border border-line bg-surface p-3 text-[11px] leading-4 text-ink-mid">
            <span className="font-medium text-ink">Squashable</span> marks
            contiguous published layers with no live leases or bookings — the
            runs a checkpoint squash flattens. The base layer always stays.
          </div>
        </section>
      </div>
    </div>
  );
}

function LayerRow({
  layer,
  index,
  total,
  maxBytes,
  squashable,
}: {
  layer: StackLayer;
  index: number;
  total: number;
  maxBytes: number;
  squashable: boolean;
}) {
  const level = total - index - 1;
  const isBase = index === total - 1;
  return (
    <li
      className={cn(
        "flex items-center gap-3 rounded border px-2 py-1.5",
        squashable ? "border-accent/40 bg-accent-soft/40" : "border-line",
      )}
    >
      <span className="w-8 shrink-0 font-mono text-[11px] text-ink-faint">
        {isBase ? "base" : `L${level}`}
      </span>
      <span className="w-40 shrink-0 truncate font-mono text-xs" title={layer.layer_id}>
        {layer.layer_id}
      </span>
      <span className="hidden min-w-0 flex-1 sm:block">
        <span
          className="block h-2 rounded-sm bg-accent/50"
          style={{ width: `${Math.max((layer.bytes / maxBytes) * 100, 2)}%` }}
          title={`${layer.bytes} bytes`}
        />
      </span>
      <span className="w-16 shrink-0 text-right font-mono text-[11px] text-ink-mid">
        {formatBytes(layer.bytes)}
      </span>
      <Tooltip
        content={`${layer.leased_by_workspaces} workspace lease(s)${layer.booked_by.length > 0 ? ` · booked by ${layer.booked_by.join(", ")}` : ""}`}
      >
        <span
          className={cn(
            "w-20 shrink-0 text-right text-[11px]",
            layer.leased_by_workspaces > 0 || layer.booked_by.length > 0
              ? "text-warn"
              : "text-ink-faint",
          )}
        >
          {layer.leased_by_workspaces} leases
          {layer.booked_by.length > 0 ? ` +${layer.booked_by.length}b` : ""}
        </span>
      </Tooltip>
      {squashable ? (
        <span className="shrink-0 rounded bg-accent/10 px-1 text-[10px] font-medium text-accent">
          squashable
        </span>
      ) : null}
    </li>
  );
}

/**
 * Indexes of layers inside a squashable run: contiguous non-base layers
 * with no live leases or bookings, in runs of at least two.
 */
function squashRuns(layers: StackLayer[]): Set<number> {
  const marks = new Set<number>();
  let runStart: number | null = null;
  const flush = (end: number) => {
    if (runStart !== null && end - runStart >= 2) {
      for (let index = runStart; index < end; index += 1) marks.add(index);
    }
    runStart = null;
  };
  for (let index = 0; index < layers.length; index += 1) {
    const layer = layers[index];
    const isBase = index === layers.length - 1;
    const free =
      !isBase && layer.leased_by_workspaces === 0 && layer.booked_by.length === 0;
    if (free && runStart === null) runStart = index;
    if (!free) flush(index);
  }
  flush(layers.length);
  return marks;
}
