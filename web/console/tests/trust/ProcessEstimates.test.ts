import { describe, expect, it } from "vitest";
import type { WorkspaceProcess, WorkspaceProcessTopology } from "@/api/observability";
import { estimateWorkspaceResources } from "@/pages/sandbox/observability/processEstimates";

describe("workspace procfs resource estimates", () => {
  it("sums current RSS and waits for a prior CPU sample", () => {
    const current = topology([
      process(11, 101, 1_048_576, 100_000),
      process(12, 102, 524_288, 200_000),
    ]);

    expect(estimateWorkspaceResources(undefined, current, null)).toEqual({
      workspace: { residentMemoryBytes: 1_572_864, cpuPercent: null },
    });
  });

  it("computes CPU time deltas over the refresh interval", () => {
    const previous = topology([process(11, 101, 1_048_576, 100_000)]);
    const current = topology([process(11, 101, 1_048_576, 600_000)]);

    expect(estimateWorkspaceResources(previous, current, 2_000).workspace.cpuPercent).toBe(25);
  });

  it("does not treat a reused PID as the same process", () => {
    const previous = topology([process(11, 101, 1_048_576, 900_000)]);
    const current = topology([process(11, 202, 1_048_576, 1_000)]);

    expect(estimateWorkspaceResources(previous, current, 2_000).workspace.cpuPercent).toBeNull();
  });

  it("keeps partial estimates when individual proc metrics are unavailable", () => {
    const previous = topology([process(11, 101, null, 100_000)]);
    const current = topology([
      process(11, 101, null, 200_000),
      process(12, null, 2_048, null),
    ]);

    expect(estimateWorkspaceResources(previous, current, 1_000)).toEqual({
      workspace: { residentMemoryBytes: 2_048, cpuPercent: 10 },
    });
  });
});

function topology(processes: WorkspaceProcess[]): WorkspaceProcessTopology {
  return {
    schema_version: 2,
    available: true,
    source: "proc_namespaces",
    error: null,
    truncated: false,
    warnings: [],
    workspaces: [{
      workspace_id: "workspace",
      state: "active",
      holder_pid: 10,
      pid_namespace: "pid:[1]",
      mount_namespace: "mnt:[2]",
      processes,
    }],
  };
}

function process(
  pid: number,
  startTimeTicks: number | null,
  residentMemoryBytes: number | null,
  cpuTimeUs: number | null,
): WorkspaceProcess {
  return {
    pid,
    namespace_pid: pid,
    parent_pid: 1,
    name: "worker",
    state: "R (running)",
    kind: "process",
    cgroup_memberships: ["0::/"],
    resident_memory_bytes: residentMemoryBytes,
    cpu_time_us: cpuTimeUs,
    start_time_ticks: startTimeTicks,
  };
}
