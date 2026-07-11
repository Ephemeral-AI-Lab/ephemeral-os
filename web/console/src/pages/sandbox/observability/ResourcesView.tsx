import { useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router";
import uPlot from "uplot";
import { Select } from "@mantine/core";
import "uplot/dist/uPlot.min.css";
import { fetchCgroup, type ResourceSample } from "@/api/observability";
import { usePoll } from "@/poll/usePoll";
import { useSandbox } from "@/pages/sandbox/SandboxContext";

const WINDOWS = [
  { label: "60s", ms: 60_000 },
  { label: "300s", ms: 300_000 },
  { label: "600s (max)", ms: 600_000 },
];

/**
 * Resource charts over the `cgroup` view: CPU and IO counters render as
 * deltas (the sample format carries monotonic counters in `deltas`), memory
 * and disk as gauges; the scope picker mirrors the op's `--scope` argument
 * (sandbox or one workspace id) and the window caps at the API's 600s max.
 */
export function ResourcesView() {
  const { sandboxId, snapshot } = useSandbox();
  const [searchParams, setSearchParams] = useSearchParams();
  const scope = searchParams.get("scope") ?? "sandbox";
  const windowMs = Number(searchParams.get("window") ?? 60_000);

  const workspaces = snapshot?.sandboxes[0]?.workspaces ?? [];

  const series = usePoll({
    key: ["observability", sandboxId, "cgroup", scope, windowMs],
    fn: () => fetchCgroup(sandboxId, scope, windowMs),
    mode: "slow",
  });

  const samples = useMemo(() => series.data?.series ?? [], [series.data]);
  const unavailable =
    samples.length > 0 &&
    samples.every((sample) => sample.metrics["cgroup_available"] === false);

  const apply = (next: { scope?: string; window?: number }) => {
    const params = new URLSearchParams(searchParams);
    if (next.scope !== undefined) params.set("scope", next.scope);
    if (next.window !== undefined) params.set("window", String(next.window));
    setSearchParams(params, { replace: true });
  };

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[11px] text-ink-faint">scope</label>
        <Select
          className="w-64"
          value={scope}
          onChange={(value) => apply({ scope: value ?? "sandbox" })}
          data={[
            { value: "sandbox", label: "sandbox" },
            ...workspaces.map((workspace) => ({
              value: workspace.workspace_id,
              label: `workspace · ${workspace.workspace_id}`,
            })),
          ]}
        />
        <label className="text-[11px] text-ink-faint">window</label>
        <Select
          className="w-36"
          value={String(windowMs)}
          onChange={(value) => apply({ window: Number(value ?? WINDOWS[0].ms) })}
          data={WINDOWS.map((window) => ({ value: String(window.ms), label: window.label }))}
        />
        <span className="ml-auto text-[11px] text-ink-faint">
          auto-refresh · {samples.length} samples
        </span>
      </div>

      {series.isError && !series.data ? (
        <div className="rounded border border-danger/40 bg-danger-soft p-2 text-xs text-ink">
          {(series.error as Error).message} — retrying automatically.
        </div>
      ) : null}

      {unavailable ? (
        <div className="rounded border border-warn/40 bg-warn-soft p-2 text-xs text-ink">
          cgroup metrics are unavailable in this container (
          {String(samples[samples.length - 1]?.metrics["cgroup_error"] ?? "")})
          — disk metrics still render for workspace scopes.
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ChartPane
          title="CPU (Δ cpu_usec / s)"
          samples={samples}
          value={(sample) => {
            const delta = sample.deltas["cpu_usec"];
            if (typeof delta !== "number") return null;
            const period = sample.sample_delta_ms ?? 1000;
            return period > 0 ? delta / (period * 1000) : null;
          }}
        />
        <ChartPane
          title="Memory (mem_cur bytes)"
          samples={samples}
          value={(sample) => {
            const mem = sample.metrics["mem_cur"];
            return typeof mem === "number" ? mem : null;
          }}
        />
        <ChartPane
          title="IO (Δ read+write bytes)"
          samples={samples}
          value={(sample) => {
            const read = sample.deltas["io_rbytes"];
            const write = sample.deltas["io_wbytes"];
            if (typeof read !== "number" && typeof write !== "number") return null;
            return (typeof read === "number" ? read : 0) + (typeof write === "number" ? write : 0);
          }}
        />
        <ChartPane
          title="Disk upperdir (bytes)"
          samples={samples}
          value={(sample) => {
            const disk = sample.metrics["disk_bytes"];
            return typeof disk === "number" ? disk : null;
          }}
        />
      </div>
    </div>
  );
}

function ChartPane({
  title,
  samples,
  value,
}: {
  title: string;
  samples: ResourceSample[];
  value: (sample: ResourceSample) => number | null;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);

  const data = useMemo(() => {
    const xs: number[] = [];
    const ys: (number | null)[] = [];
    for (const sample of samples) {
      const y = value(sample);
      if (y === null) continue;
      xs.push(sample.ts / 1000);
      ys.push(y);
    }
    return [xs, ys] as uPlot.AlignedData;
  }, [samples, value]);

  const hasData = data[0].length > 1;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !hasData) return;
    const plot = new uPlot(
      {
        width: host.clientWidth,
        height: 140,
        legend: { show: false },
        cursor: { show: false },
        series: [
          {},
          {
            stroke: "#2563eb",
            width: 1.4,
            fill: "rgba(37, 99, 235, 0.08)",
            spanGaps: true,
          },
        ],
        axes: [
          { stroke: "#8a93a2", grid: { show: false }, ticks: { show: false } },
          {
            stroke: "#8a93a2",
            grid: { stroke: "#eef0f4", width: 1 },
            ticks: { show: false },
            size: 56,
          },
        ],
      },
      data,
      host,
    );
    plotRef.current = plot;
    const onResize = () => plot.setSize({ width: host.clientWidth, height: 140 });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      plot.destroy();
      plotRef.current = null;
    };
  }, [data, hasData]);

  return (
    <section className="rounded-lg border border-line bg-surface p-3">
      <h3 className="mb-2 text-xs font-semibold text-ink-mid">{title}</h3>
      {hasData ? (
        <div ref={hostRef} />
      ) : (
        <div className="flex h-[140px] items-center justify-center text-[11px] text-ink-faint">
          no samples in this window
        </div>
      )}
    </section>
  );
}
