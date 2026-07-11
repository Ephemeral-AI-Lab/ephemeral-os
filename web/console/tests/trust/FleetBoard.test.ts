import { describe, expect, it } from "vitest";
import type { SandboxList } from "@/api/types";
import type { SnapshotResult } from "@/api/observability";
import { currentFleetList, hasFleetActivity, stabilizeFleetList } from "@/pages/fleet/FleetBoard";

const slow: SandboxList = {
  sandboxes: [
    {
      id: "current",
      workspace_root: "/work/current",
      state: "ready",
      daemon: null,
      daemon_http: null,
      shared_base: null,
    },
  ],
};

const staleFast: SandboxList = {
  sandboxes: [
    {
      id: "stale",
      workspace_root: "/work/stale",
      state: "creating",
      daemon: null,
      daemon_http: null,
      shared_base: null,
    },
  ],
};

const activeSnapshot: SnapshotResult = {
  sandboxes: [
    {
      sandbox_id: "current",
      lifecycle_state: "ready",
      availability: "available",
      sampled_at_unix_ms: 1,
      errors: [],
      daemon: null,
      resources: { latest: null, history: [] },
      workspaces: [
        {
          workspace_id: "workspace-current",
          lifecycle_state: "running",
          network_profile: "shared",
          layers: { base_root_hash: "root", layer_count: 0 },
          namespace_fd_count: 0,
          resources: { latest: null, history: [] },
          active_namespace_executions: [
            {
              namespace_execution_id: "active-command",
              operation: "exec",
              lifecycle_state: "running",
            },
          ],
        },
      ],
      stack: { layer_count: 0, layers_bytes: 0, active_leases: 0 },
    },
  ],
};

describe("Fleet polling generation contract", () => {
  it("drops the stale fast generation after lifecycle polling stops", () => {
    expect(currentFleetList(slow, staleFast, false)).toEqual(slow);
  });

  it("uses the fast list for both cards and summary while lifecycle polling is active", () => {
    expect(currentFleetList(slow, staleFast, true)).toEqual(staleFast);
  });

  it("starts fast polling for a ready sandbox with an active namespace execution", () => {
    expect(hasFleetActivity(slow, activeSnapshot, null)).toBe(true);
  });

  it("keeps existing cards in place when a fast generation arrives in a different order", () => {
    const reordered: SandboxList = {
      sandboxes: [staleFast.sandboxes[0], slow.sandboxes[0]],
    };

    expect(stabilizeFleetList(reordered, ["current", "stale"])?.sandboxes.map((record) => record.id)).toEqual([
      "current",
      "stale",
    ]);
  });
});
