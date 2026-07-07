import { useOutletContext } from "react-router";
import type { SandboxRecord } from "@/api/types";
import type { SnapshotResult } from "@/api/observability";

export interface SandboxContext {
  sandboxId: string;
  record: SandboxRecord | null;
  snapshot: SnapshotResult | null;
  recordError: unknown;
}

export function useSandbox(): SandboxContext {
  return useOutletContext<SandboxContext>();
}
