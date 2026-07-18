import { keepPreviousData, useQueries } from "@tanstack/react-query";
import { fetchCgroup, type ResourceSample } from "@/api/observability";
import type { SandboxRecord } from "@/api/types";
import { SLOW_POLL_MS } from "@/poll/usePoll";

export const FLEET_USAGE_WINDOW_MS = 10_000;
const MIN_CPU_SAMPLE_MS = 250;

export interface SandboxCurrentUsage {
  cpuPercent: number | null;
  memoryBytes: number | null;
  sampledAt: number | null;
}

export function currentUsageFromSeries(samples: ResourceSample[]): SandboxCurrentUsage {
  const latest = samples[samples.length - 1];
  let memoryBytes: number | null = null;
  let cpuPercent: number | null = null;

  for (let index = samples.length - 1; index >= 0; index -= 1) {
    const memory = samples[index]?.metrics["mem_cur"];
    if (typeof memory === "number") {
      memoryBytes = memory;
      break;
    }
  }

  for (let index = samples.length - 1; index >= 0; index -= 1) {
    const sample = samples[index];
    const cpuDelta = sample?.deltas["cpu_usec"];
    const sampleDeltaMs = sample?.sample_delta_ms;
    if (
      typeof cpuDelta === "number" &&
      typeof sampleDeltaMs === "number" &&
      sampleDeltaMs >= MIN_CPU_SAMPLE_MS
    ) {
      cpuPercent = (cpuDelta / (sampleDeltaMs * 1_000)) * 100;
      break;
    }
  }

  return {
    cpuPercent,
    memoryBytes,
    sampledAt: latest?.ts ?? null,
  };
}

export function useFleetCurrentUsage(records: SandboxRecord[]) {
  const queries = useQueries({
    queries: records.map((record) => ({
      queryKey: ["sandbox", record.id, "current-usage"],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchCgroup(record.id, "sandbox", FLEET_USAGE_WINDOW_MS, signal),
      enabled: record.state === "ready",
      retry: false,
      placeholderData: keepPreviousData,
      refetchOnWindowFocus: "always" as const,
      refetchIntervalInBackground: false,
      refetchInterval: SLOW_POLL_MS,
    })),
  });

  const data = new Map<string, SandboxCurrentUsage>();
  records.forEach((record, index) => {
    const series = queries[index]?.data?.series;
    if (series) data.set(record.id, currentUsageFromSeries(series));
  });

  return {
    data,
    isFetching: queries.some((query) => query.isFetching),
    error: queries.find((query) => query.error)?.error ?? null,
  };
}
