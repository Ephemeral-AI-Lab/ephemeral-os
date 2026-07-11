import { describe, expect, it } from "vitest";
import type { SandboxList } from "@/api/types";
import { currentFleetList } from "@/pages/fleet/FleetBoard";

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

describe("Fleet polling generation contract", () => {
  it("drops the stale fast generation after lifecycle polling stops", () => {
    expect(currentFleetList(slow, staleFast, false)).toEqual(slow);
  });

  it("uses the fast list for both cards and summary while lifecycle polling is active", () => {
    expect(currentFleetList(slow, staleFast, true)).toEqual(staleFast);
  });
});
