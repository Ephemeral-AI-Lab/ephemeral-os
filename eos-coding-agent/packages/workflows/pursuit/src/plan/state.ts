import type {
  AgentRunId,
  AttemptId,
  PlanId,
  PursuitEntityRunStatus,
} from "../../contracts/pursuit.js";

export interface PlanState {
  readonly id: PlanId;
  readonly attemptId: AttemptId;
  readonly agentRunId: AgentRunId | null;
  readonly status: PursuitEntityRunStatus;
  readonly declaredLegGoal: string | null;
  readonly declaredNextLegGoal: string | null;
  readonly legGoalVersion: number;
  readonly summary: string | null;
}
