import { useOutletContext } from "react-router";
import type { SandboxRecord } from "@/api/types";

export interface SandboxContext {
  sandboxId: string;
  record: SandboxRecord | null;
}

export function useSandbox(): SandboxContext {
  return useOutletContext<SandboxContext>();
}
