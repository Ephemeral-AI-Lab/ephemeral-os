import { useEffect, useMemo, useState } from "react";
import { Button, Select, Tooltip } from "@mantine/core";
import {
  fetchLayerStack,
  fetchLayerStackLayer,
  fetchLayerStackWorkspace,
  type LayerStackLayerEntry,
  type LayerStackLayerEntryKind,
  type LayerStackLayerResult,
  type LayerStackWorkspaceResult,
  type StackLayer,
} from "@/api/observability";
import { usePoll } from "@/poll/usePoll";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { SquashDialog } from "@/components/SquashDialog";
import { ResourceSparkline } from "@/components/ResourceSparkline";
import { cn } from "@/lib/cn";
import { formatBytes, shortHash } from "@/lib/format";

const ALL_WORKSPACES = "__all__";
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

export function LayerStackView() {
  const { sandboxId, snapshot } = useSandbox();
  const [workspaceId, setWorkspaceId] = useState(ALL_WORKSPACES);
  const [selectedLayerId, setSelectedLayerId] = useState<string | null>(null);

  const workspaces = snapshot?.sandboxes[0]?.workspaces ?? [];
  const stack = usePoll({
    key: ["observability", sandboxId, "layerstack"],
    fn: () => fetchLayerStack(sandboxId),
    mode: "slow",
  });
  const workspaceDetail = usePoll({
    key: ["observability", sandboxId, "layerstack", "workspace", workspaceId],
    fn: () => fetchLayerStackWorkspace(sandboxId, workspaceId),
    mode: "slow",
    enabled: workspaceId !== ALL_WORKSPACES,
  });
  const layerDetail = usePoll({
    key: ["observability", sandboxId, "layerstack", "layer", selectedLayerId ?? ""],
    fn: () => fetchLayerStackLayer(sandboxId, selectedLayerId ?? ""),
    mode: "slow",
    enabled: selectedLayerId !== null,
  });

  const layers = useMemo(() => stack.data?.layers ?? [], [stack.data]);
  const squashableRuns = useMemo(() => squashRuns(layers), [layers]);
  const depthTrend = stack.data ? recordDepth(sandboxId, layers.length) : [];
  const maxBytes = Math.max(1, ...layers.map((layer) => layer.bytes));
  const selectedLayer =
    layers.find((layer) => layer.layer_id === selectedLayerId) ?? null;
  const workspaceData =
    workspaceDetail.data?.workspace === workspaceId ? workspaceDetail.data : null;
  const layerData =
    layerDetail.data?.layer_id === selectedLayerId ? layerDetail.data : null;

  useEffect(() => {
    if (workspaceId === ALL_WORKSPACES) return;
    if (!workspaces.some((workspace) => workspace.workspace_id === workspaceId)) {
      setWorkspaceId(ALL_WORKSPACES);
    }
  }, [workspaceId, workspaces]);

  useEffect(() => {
    if (layers.length === 0) {
      if (selectedLayerId !== null) setSelectedLayerId(null);
      return;
    }
    if (
      selectedLayerId === null ||
      !layers.some((layer) => layer.layer_id === selectedLayerId)
    ) {
      setSelectedLayerId(layers[0].layer_id);
    }
  }, [layers, selectedLayerId]);

  if (stack.isError) {
    return (
      <div className="m-4 rounded border border-danger/40 bg-danger-soft p-3 text-xs">
        {(stack.error as Error).message}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-mid">
        <span>
          manifest v
          <span className="font-mono text-ink">
            {stack.data?.manifest_version ?? "..."}
          </span>
        </span>
        <span className="font-mono" title="root hash">
          {stack.data ? shortHash(stack.data.root_hash, 12) : "..."}
        </span>
        <span>
          {layers.length} layers · {formatBytes(stack.data?.total_bytes ?? 0)}
        </span>
        <span>{stack.data?.active_lease_count ?? 0} active leases</span>
        <label className="ml-auto text-[11px] text-ink-faint">workspace</label>
        <Select
          className="w-64"
          value={workspaceId}
          onChange={(value) => setWorkspaceId(value ?? ALL_WORKSPACES)}
          data={[
            { value: ALL_WORKSPACES, label: "all workspaces" },
            ...workspaces.map((workspace) => ({
              value: workspace.workspace_id,
              label: `workspace · ${workspace.workspace_id}`,
            })),
          ]}
        />
        <SquashDialog
          sandboxId={sandboxId}
          layerCount={layers.length}
          trigger={(open) => (
              <Button size="compact-xs" variant="filled" onClick={open}>
                Squash ({layers.length})
              </Button>
          )}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
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
                  selected={layer.layer_id === selectedLayerId}
                  onSelect={setSelectedLayerId}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="flex min-w-0 flex-col gap-3">
          <LayerDeltaPanel
            layer={selectedLayer}
            data={layerData}
            isError={layerDetail.isError && !layerData}
            error={layerDetail.error}
            isFetching={layerDetail.isFetching && !layerData}
          />
          <WorkspacePanel
            workspaceId={workspaceId}
            workspaceCount={workspaces.length}
            data={workspaceData}
            isError={workspaceDetail.isError && !workspaceData}
            error={workspaceDetail.error}
            isFetching={workspaceDetail.isFetching && !workspaceData}
          />
          <div className="rounded-lg border border-line bg-surface p-3">
            <h3 className="mb-2 text-xs font-semibold text-ink-mid">
              stack depth
            </h3>
            <ResourceSparkline values={depthTrend} width={220} height={36} label="stack depth" />
            <p className="mt-1 text-[11px] text-ink-faint">
              {layers.length} now · trend accumulates while this view polls
            </p>
          </div>
          <div className="rounded-lg border border-line bg-surface p-3 text-[11px] leading-4 text-ink-mid">
            <span className="font-medium text-ink">Squashable</span> marks
            contiguous published layers with no live leases or bookings. The
            base layer stays.
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
  selected,
  onSelect,
}: {
  layer: StackLayer;
  index: number;
  total: number;
  maxBytes: number;
  squashable: boolean;
  selected: boolean;
  onSelect: (layerId: string) => void;
}) {
  const level = total - index - 1;
  const isBase = index === total - 1;
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => onSelect(layer.layer_id)}
        className={cn(
          "flex w-full items-center gap-3 rounded border px-2 py-1.5 text-left",
          selected
            ? "border-accent bg-accent-soft"
            : squashable
              ? "border-accent/40 bg-accent-soft/40 hover:bg-accent-soft"
              : "border-line hover:bg-surface-hover",
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
          label={`${layer.leased_by_workspaces} workspace lease(s)${layer.booked_by.length > 0 ? ` · booked by ${layer.booked_by.join(", ")}` : ""}`}
          openDelay={300}
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
      </button>
    </li>
  );
}

function LayerDeltaPanel({
  layer,
  data,
  isError,
  error,
  isFetching,
}: {
  layer: StackLayer | null;
  data: LayerStackLayerResult | null;
  isError: boolean;
  error: unknown;
  isFetching: boolean;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-xs font-semibold text-ink-mid">changed paths</h3>
        {layer ? (
          <span className="min-w-0 truncate font-mono text-[11px] text-ink-faint">
            {shortHash(layer.layer_id, 10)}
          </span>
        ) : null}
      </div>
      {layer ? (
        <p className="mb-2 text-[11px] text-ink-faint">
          {formatBytes(layer.bytes)}
        </p>
      ) : null}
      {isError ? (
        <div className="rounded border border-danger/40 bg-danger-soft p-2 text-[11px] text-ink">
          {(error as Error).message}
        </div>
      ) : isFetching ? (
        <p className="text-[11px] text-ink-faint">loading...</p>
      ) : data ? (
        <LayerEntryList entries={data.entries} truncated={data.truncated} />
      ) : (
        <p className="text-[11px] text-ink-faint">no layer selected</p>
      )}
    </div>
  );
}

function LayerEntryList({
  entries,
  truncated,
}: {
  entries: LayerStackLayerEntry[];
  truncated: boolean;
}) {
  if (entries.length === 0) {
    return <p className="text-[11px] text-ink-faint">no changed paths</p>;
  }
  return (
    <div className="min-w-0">
      <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto pr-1">
        {entries.map((entry) => (
          <li key={`${entry.kind}:${entry.path}`} className="flex min-w-0 items-center gap-2">
            <span
              className={cn(
                "w-16 shrink-0 rounded border px-1 py-0.5 text-center text-[10px]",
                entryKindClass(entry.kind),
              )}
            >
              {entryKindLabel(entry.kind)}
            </span>
            <span className="min-w-0 truncate font-mono text-[11px]" title={entry.path}>
              {entry.path}
            </span>
          </li>
        ))}
      </ul>
      {truncated ? (
        <p className="mt-2 text-[11px] text-warn">showing first 500 entries</p>
      ) : null}
    </div>
  );
}

function WorkspacePanel({
  workspaceId,
  workspaceCount,
  data,
  isError,
  error,
  isFetching,
}: {
  workspaceId: string;
  workspaceCount: number;
  data: LayerStackWorkspaceResult | null;
  isError: boolean;
  error: unknown;
  isFetching: boolean;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <h3 className="mb-2 text-xs font-semibold text-ink-mid">workspace mounts</h3>
      {workspaceId === ALL_WORKSPACES ? (
        <p className="text-[11px] text-ink-faint">
          {workspaceCount} live workspace{workspaceCount === 1 ? "" : "s"}
        </p>
      ) : isError ? (
        <div className="rounded border border-danger/40 bg-danger-soft p-2 text-[11px] text-ink">
          {(error as Error).message}
        </div>
      ) : isFetching ? (
        <p className="text-[11px] text-ink-faint">loading...</p>
      ) : data ? (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-[11px] text-ink-faint">
            <span className="font-mono text-ink-mid">{data.workspace}</span>
            <span>{formatBytes(data.upper_bytes ?? 0)} upper</span>
          </div>
          <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto pr-1">
            {data.mounts.map((mount) => (
              <li key={mount.layer_id} className="min-w-0 rounded border border-line px-2 py-1">
                <div className="truncate font-mono text-[11px]" title={mount.layer_id}>
                  {mount.layer_id}
                </div>
                <div className="text-[10px] text-ink-faint">
                  shared with {mount.shared_with.length}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-[11px] text-ink-faint">no workspace selected</p>
      )}
    </div>
  );
}

function entryKindLabel(kind: LayerStackLayerEntryKind): string {
  switch (kind) {
    case "directory":
      return "dir";
    case "opaque_dir":
      return "opaque";
    case "symlink":
      return "link";
    default:
      return kind;
  }
}

function entryKindClass(kind: LayerStackLayerEntryKind): string {
  switch (kind) {
    case "delete":
      return "border-danger/30 bg-danger-soft text-danger";
    case "directory":
      return "border-accent/30 bg-accent-soft text-accent";
    case "opaque_dir":
      return "border-warn/40 bg-warn-soft text-warn";
    default:
      return "border-line bg-surface-hover text-ink-mid";
  }
}

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
