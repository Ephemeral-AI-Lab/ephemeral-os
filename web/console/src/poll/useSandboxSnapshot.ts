import { useRef } from "react";
import type { SandboxRecord } from "@/api/types";
import {
  fetchSandboxSnapshot,
  type SnapshotResult,
} from "@/api/observability";
import { usePoll } from "@/poll/usePoll";

export function snapshotHasActivity(
  record: SandboxRecord | null,
  snapshot?: SnapshotResult | null,
): boolean {
  if (record?.state !== "ready") return false;
  return (snapshot?.sandboxes ?? []).some(
    (sandbox) =>
      sandbox.sandbox_id === record.id &&
      sandbox.workspaces.some(
        (workspace) => workspace.active_namespace_executions.length > 0,
      ),
  );
}

export function shouldRequestSandboxSnapshot(
  record: SandboxRecord | null,
  snapshot: SnapshotResult | undefined,
  lastSnapshotRevision: number | null,
): boolean {
  if (record?.state !== "ready") return false;
  if (snapshot === undefined) return true;
  if (snapshotHasActivity(record, snapshot)) return true;
  return (
    typeof record.activity_revision === "number" &&
    record.activity_revision !== lastSnapshotRevision
  );
}

/**
 * Daemon snapshot polling is revision-gated. Manager record polling discovers
 * mutations; this query runs once initially, once per new revision, or while a
 * previously returned snapshot says an execution is active. An idle disabled
 * query is not refetched merely because the browser regains focus.
 */
export function useSandboxSnapshot(
  sandboxId: string,
  record: SandboxRecord | null,
) {
  const lastSnapshotRevision = useRef<number | null>(null);
  return usePoll<SnapshotResult>({
    key: ["sandbox", sandboxId, "snapshot"],
    fn: async (signal) => {
      const revision = record?.activity_revision;
      const result = await fetchSandboxSnapshot(sandboxId, signal);
      lastSnapshotRevision.current = typeof revision === "number" ? revision : null;
      return result;
    },
    mode: (data) => (snapshotHasActivity(record, data) ? "fast" : "slow"),
    enabled: (data) =>
      sandboxId !== "" &&
      shouldRequestSandboxSnapshot(record, data, lastSnapshotRevision.current),
    refetchOnWindowFocus: false,
  });
}
