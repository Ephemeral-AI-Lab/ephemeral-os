import { describe, expect, it } from "vitest";
import type { SandboxRecord } from "@/api/types";
import type { SnapshotResult } from "@/api/observability";
import { snapshotHasActivity } from "@/pages/sandbox/SandboxDetail";
import { shouldRequestSandboxSnapshot } from "@/poll/useSandboxSnapshot";

const readyRecord: SandboxRecord = {
  id: "sandbox-a",
  workspace_root: "/work",
  state: "ready",
  daemon: null,
  daemon_http: null,
  shared_base: null,
  activity_revision: 7,
};

const activeSnapshot: SnapshotResult = {
  sandboxes: [
    {
      sandbox_id: "sandbox-a",
      lifecycle_state: "ready",
      availability: "available",
      sampled_at_unix_ms: 0,
      errors: [],
      daemon: null,
      resources: { latest: null, history: [] },
      workspaces: [
        {
          workspace_id: "workspace-a",
          lifecycle_state: "active",
          network_profile: "shared",
          layers: { base_root_hash: "root", layer_count: 0 },
          namespace_fd_count: 0,
          resources: { latest: null, history: [] },
          active_namespace_executions: [
            {
              namespace_execution_id: "command-a",
              operation: "exec_command",
              lifecycle_state: "running",
            },
          ],
        },
      ],
      stack: { layer_count: 0, layers_bytes: 0, active_leases: 0 },
    },
  ],
};

describe("sandbox snapshot polling contract", () => {
  it("uses fast polling for a ready sandbox with active work", () => {
    expect(snapshotHasActivity(readyRecord, activeSnapshot)).toBe(true);
  });

  it("ignores timestamp and resource-counter churn at a stable revision", () => {
    const idle = structuredClone(activeSnapshot);
    idle.sandboxes[0].workspaces[0].active_namespace_executions = [];
    const changed = structuredClone(idle);
    changed.sandboxes[0].sampled_at_unix_ms = 99_999;
    changed.sandboxes[0].resources.latest = {
      ts: 99_999,
      sample_delta_ms: 2_000,
      metrics: { mem_cur: 123_456 },
      deltas: { cpu_usec: 1_000 },
    };

    expect(shouldRequestSandboxSnapshot(readyRecord, idle, 7)).toBe(false);
    expect(shouldRequestSandboxSnapshot(readyRecord, changed, 7)).toBe(false);
  });

  it("requests exactly when the manager activity revision changes", () => {
    const idle = structuredClone(activeSnapshot);
    idle.sandboxes[0].workspaces[0].active_namespace_executions = [];
    expect(shouldRequestSandboxSnapshot(readyRecord, undefined, null)).toBe(true);
    expect(shouldRequestSandboxSnapshot(readyRecord, idle, 7)).toBe(false);
    expect(
      shouldRequestSandboxSnapshot({ ...readyRecord, activity_revision: 8 }, idle, 7),
    ).toBe(true);
  });
});
