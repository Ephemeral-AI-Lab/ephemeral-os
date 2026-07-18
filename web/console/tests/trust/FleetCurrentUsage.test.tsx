import { act, render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CgroupSeries, ResourceSample, SandboxSnapshot } from "@/api/observability";
import type { SandboxRecord } from "@/api/types";
import { SandboxCard } from "@/pages/fleet/SandboxCard";
import {
  currentUsageFromSeries,
  FLEET_USAGE_WINDOW_MS,
  useFleetCurrentUsage,
} from "@/poll/useFleetCurrentUsage";

const mocks = vi.hoisted(() => ({
  fetchCgroup: vi.fn(),
}));

vi.mock("@/api/observability", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/observability")>()),
  fetchCgroup: mocks.fetchCgroup,
}));

function record(id: string, state: SandboxRecord["state"] = "ready"): SandboxRecord {
  return {
    id,
    workspace_root: `/work/${id}`,
    state,
    daemon: null,
    daemon_http: null,
    shared_base: null,
    activity_revision: 0,
  };
}

function sample(
  ts: number,
  sampleDeltaMs: number,
  cpuDelta: number,
  memory: number,
): ResourceSample {
  return {
    ts,
    sample_delta_ms: sampleDeltaMs,
    metrics: { mem_cur: memory },
    deltas: { cpu_usec: cpuDelta },
  };
}

function series(samples: ResourceSample[]): CgroupSeries {
  return {
    view: "cgroup",
    scope: "sandbox",
    series: samples,
    topology: {
      schema_version: 2,
      available: false,
      source: null,
      error: null,
      truncated: false,
      warnings: [],
      workspaces: [],
    },
  };
}

function snapshot(id: string): SandboxSnapshot {
  return {
    sandbox_id: id,
    lifecycle_state: "ready",
    availability: "available",
    sampled_at_unix_ms: 1,
    errors: [],
    daemon: null,
    resources: { latest: null, history: [] },
    workspaces: [],
    stack: { layer_count: 0, layers_bytes: 0, active_leases: 0 },
  };
}

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Number.POSITIVE_INFINITY },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MemoryRouter>
        <MantineProvider>
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        </MantineProvider>
      </MemoryRouter>
    );
  };
}

function Harness({ records }: { records: SandboxRecord[] }) {
  const usage = useFleetCurrentUsage(records);
  return <div>{usage.data.get("sandbox-a")?.memoryBytes ?? "unavailable"}</div>;
}

async function flush(milliseconds = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

describe("Fleet current usage", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.fetchCgroup.mockReset();
    mocks.fetchCgroup.mockResolvedValue(
      series([
        sample(1_000, 2_000, 20_000, 20_000_000),
        sample(1_005, 5, 0, 21_000_000),
      ]),
    );
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("uses the newest memory gauge and newest stable CPU interval", () => {
    expect(
      currentUsageFromSeries([
        sample(1_000, 2_000, 20_000, 20_000_000),
        sample(1_005, 5, 0, 21_000_000),
      ]),
    ).toEqual({
      cpuPercent: 1,
      memoryBytes: 21_000_000,
      sampledAt: 1_005,
    });
  });

  it("polls current resources only for ready sandboxes", async () => {
    render(<Harness records={[record("sandbox-a"), record("sandbox-b", "failed")]} />, {
      wrapper: createWrapper(),
    });
    await flush();

    expect(mocks.fetchCgroup).toHaveBeenCalledTimes(1);
    expect(mocks.fetchCgroup).toHaveBeenCalledWith(
      "sandbox-a",
      "sandbox",
      FLEET_USAGE_WINDOW_MS,
      expect.any(AbortSignal),
    );
    expect(screen.getByText("21000000")).toBeTruthy();

    await flush(2_000);
    expect(mocks.fetchCgroup).toHaveBeenCalledTimes(2);
  });

  it("renders current values without CPU or memory history graphics", () => {
    render(
      <SandboxCard
        record={record("sandbox-a")}
        snapshot={snapshot("sandbox-a")}
        usage={{ cpuPercent: 1, memoryBytes: 21_000_000, sampledAt: 1_005 }}
        createLogs={undefined}
      />,
      { wrapper: createWrapper() },
    );

    expect(screen.getByText("Current usage")).toBeTruthy();
    expect(screen.getByText("1.0%")).toBeTruthy();
    expect(screen.getByText("21.0 MB")).toBeTruthy();
    expect(screen.queryByRole("img", { name: "CPU history" })).toBeNull();
    expect(screen.queryByRole("img", { name: "Memory history" })).toBeNull();
  });
});
