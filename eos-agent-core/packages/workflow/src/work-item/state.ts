import type {
  AgentRunId,
  PlanId,
  WorkItemId,
  WorkflowEntityRunStatus,
} from "@eos/contracts";

/** One materialized work item, decorated onto the frozen `WorkflowTree`. */
export interface WorkItemState {
  readonly id: WorkItemId;
  readonly planId: PlanId;
  readonly agentName: string;
  readonly agentRunId: AgentRunId | null;
  readonly status: WorkflowEntityRunStatus;
  readonly description: string;
  readonly spec: string;
  readonly needs: readonly WorkItemId[];
  readonly summary: string | null;
  readonly outcome: string | null;
}

/** Ready = every dependency terminal-`Success` among the attempt's items. */
export function workItemReady(
  needs: readonly string[],
  statusOf: (id: string) => WorkflowEntityRunStatus | undefined,
): boolean {
  return needs.every((id) => statusOf(id) === "Success");
}
