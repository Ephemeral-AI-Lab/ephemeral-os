import { act, render } from "@testing-library/react";
import {
  focusManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SnapshotResult } from "@/api/observability";
import type { SandboxRecord } from "@/api/types";
import { usePoll } from "@/poll/usePoll";
import { useSandboxSnapshot } from "@/poll/useSandboxSnapshot";

const mocks = vi.hoisted(() => ({
  fetchSandboxSnapshot: vi.fn(),
}));

vi.mock("@/api/observability", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/observability")>()),
  fetchSandboxSnapshot: mocks.fetchSandboxSnapshot,
}));

const idleSnapshot: SnapshotResult = {
  sandboxes: [
    {
      sandbox_id: "sandbox-a",
      lifecycle_state: "ready",
      availability: "available",
      sampled_at_unix_ms: 1,
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
          active_namespace_executions: [],
        },
      ],
      stack: { layer_count: 0, layers_bytes: 0, active_leases: 0 },
    },
  ],
};

function activeSnapshot(): SnapshotResult {
  const snapshot = structuredClone(idleSnapshot);
  snapshot.sandboxes[0].workspaces[0].active_namespace_executions = [
    {
      namespace_execution_id: "execution-a",
      operation: "exec_command",
      lifecycle_state: "running",
    },
  ];
  return snapshot;
}

function Harness({ inspect }: { inspect: () => Promise<SandboxRecord> }) {
  const record = usePoll({
    key: ["test", "inspect"],
    fn: inspect,
    mode: "slow",
  });
  useSandboxSnapshot("sandbox-a", record.data ?? null);
  return null;
}

function SnapshotHarness({ record }: { record: SandboxRecord }) {
  useSandboxSnapshot("sandbox-a", record);
  return null;
}

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Number.POSITIVE_INFINITY },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

async function flush(milliseconds = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

describe("revision-gated sandbox snapshots", () => {
  let record: SandboxRecord;
  let nextSnapshot: SnapshotResult;

  beforeEach(() => {
    vi.useFakeTimers();
    record = {
      id: "sandbox-a",
      workspace_root: "/work",
      state: "ready",
      daemon: null,
      daemon_http: null,
      shared_base: null,
      activity_revision: 0,
    };
    nextSnapshot = structuredClone(idleSnapshot);
    mocks.fetchSandboxSnapshot.mockReset();
    mocks.fetchSandboxSnapshot.mockImplementation(async () =>
      structuredClone(nextSnapshot),
    );
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  afterEach(() => {
    focusManager.setFocused(undefined);
    vi.useRealTimers();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  it("keeps manager polling but performs no idle daemon snapshots", async () => {
    const inspect = vi.fn(async () => structuredClone(record));
    render(<Harness inspect={inspect} />, { wrapper: createWrapper() });
    await flush();
    expect(inspect).toHaveBeenCalledTimes(1);
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(1);

    await flush(10_000);
    expect(inspect.mock.calls.length).toBeGreaterThan(1);
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(1);
  });

  it("takes one snapshot for a revision and polls only until inactive", async () => {
    const view = render(<SnapshotHarness record={record} />, {
      wrapper: createWrapper(),
    });
    await flush();
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(1);

    nextSnapshot = activeSnapshot();
    view.rerender(
      <SnapshotHarness record={{ ...record, activity_revision: 1 }} />,
    );
    await flush();
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(2);

    nextSnapshot = structuredClone(idleSnapshot);
    await flush(400);
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(3);
    await flush(5_000);
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(3);
  });

  it("hides all polling and focus checks revision without snapshotting", async () => {
    const inspect = vi.fn(async () => structuredClone(record));
    render(<Harness inspect={inspect} />, { wrapper: createWrapper() });
    await flush();
    const inspectBeforeHide = inspect.mock.calls.length;
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    focusManager.setFocused(false);
    await flush(10_000);
    expect(inspect).toHaveBeenCalledTimes(inspectBeforeHide);
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    focusManager.setFocused(true);
    await flush();
    expect(inspect).toHaveBeenCalledTimes(inspectBeforeHide + 1);
    expect(mocks.fetchSandboxSnapshot).toHaveBeenCalledTimes(1);
  });
});
