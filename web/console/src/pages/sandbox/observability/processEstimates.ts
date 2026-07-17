import type { WorkspaceProcess, WorkspaceProcessTopology } from "@/api/observability";

export interface WorkspaceResourceEstimate {
  residentMemoryBytes: number | null;
  cpuPercent: number | null;
}

export function estimateWorkspaceResources(
  previous: WorkspaceProcessTopology | undefined,
  current: WorkspaceProcessTopology,
  elapsedMs: number | null,
): Record<string, WorkspaceResourceEstimate> {
  const previousProcesses = previous === undefined ? undefined : indexProcesses(previous);
  const canEstimateCpu = previousProcesses !== undefined && elapsedMs !== null && elapsedMs > 0;
  const estimates: Record<string, WorkspaceResourceEstimate> = {};

  for (const workspace of current.workspaces) {
    let residentMemoryBytes = 0;
    let hasMemoryEstimate = false;
    let cpuDeltaUs = 0;
    let hasCpuEstimate = false;

    for (const process of workspace.processes) {
      if (isNonNegativeNumber(process.resident_memory_bytes)) {
        residentMemoryBytes += process.resident_memory_bytes;
        hasMemoryEstimate = true;
      }

      if (!canEstimateCpu) continue;
      const key = processIdentity(process);
      if (key === null || !isNonNegativeNumber(process.cpu_time_us)) continue;
      const previousCpuTimeUs = previousProcesses.get(`${workspace.workspace_id}:${key}`);
      if (previousCpuTimeUs === undefined || process.cpu_time_us < previousCpuTimeUs) continue;
      cpuDeltaUs += process.cpu_time_us - previousCpuTimeUs;
      hasCpuEstimate = true;
    }

    estimates[workspace.workspace_id] = {
      residentMemoryBytes: hasMemoryEstimate ? residentMemoryBytes : null,
      cpuPercent: hasCpuEstimate && elapsedMs !== null
        ? (cpuDeltaUs / (elapsedMs * 1_000)) * 100
        : null,
    };
  }

  return estimates;
}

function indexProcesses(topology: WorkspaceProcessTopology): Map<string, number> {
  const processes = new Map<string, number>();
  for (const workspace of topology.workspaces) {
    for (const process of workspace.processes) {
      const key = processIdentity(process);
      if (key !== null && isNonNegativeNumber(process.cpu_time_us)) {
        processes.set(`${workspace.workspace_id}:${key}`, process.cpu_time_us);
      }
    }
  }
  return processes;
}

function processIdentity(process: WorkspaceProcess): string | null {
  return isNonNegativeNumber(process.start_time_ticks)
    ? `${process.pid}:${process.start_time_ticks}`
    : null;
}

function isNonNegativeNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}
