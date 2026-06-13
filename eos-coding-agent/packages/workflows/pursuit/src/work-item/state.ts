import type {
  AgentRunId,
  PlanId,
  WorkItemId,
  WorkItemRunStatus,
} from "../../contracts/pursuit.js";

export interface WorkItemState {
  readonly id: WorkItemId;
  readonly planId: PlanId;
  readonly agentName: string;
  readonly agentRunId: AgentRunId | null;
  readonly status: WorkItemRunStatus;
  readonly title: string;
  readonly spec: string;
  readonly dependsOn: readonly WorkItemId[];
  readonly legGoalVersion: number;
  readonly summary: string | null;
  readonly outcome: string | null;
}

export function workItemReady(
  dependsOn: readonly string[],
  statusOf: (id: string) => WorkItemRunStatus | undefined,
): boolean {
  return dependsOn.every((id) => statusOf(id) === "Success");
}
