import { describe, expect, it } from "vitest";
import type { SandboxRecord } from "@/api/types";
import type { SnapshotResult } from "@/api/observability";
import { snapshotHasActivity } from "@/pages/sandbox/SandboxDetail";

const readyRecord: SandboxRecord = {
  id: "sandbox-a",
  workspace_root: "/work",
  state: "ready",
  daemon: null,
  daemon_http: null,
  shared_base: null,
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
});
