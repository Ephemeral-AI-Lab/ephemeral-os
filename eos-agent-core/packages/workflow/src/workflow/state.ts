import type {
  AgentRunId,
  WorkflowEntityRunStatus,
  WorkflowId,
} from "@eos/contracts";

/** The root workflow state; `currentGoal` is derived, never stored. */
export interface WorkflowState {
  readonly id: WorkflowId;
  readonly parentRunId: AgentRunId;
  readonly originalGoal: string;
  /** Head of the deferral chain: the latest iteration's goal in effect. */
  readonly currentGoal: string;
  readonly status: WorkflowEntityRunStatus;
  readonly closedAt: string | null;
}

/**
 * §8 goal-chain derivation: `original_goal` for the first iteration,
 * otherwise the predecessor's effective deferral (§6 invariant 2 - a
 * successor exists only because its predecessor closed with one).
 */
export function deriveGoalChain(
  originalGoal: string,
  effectiveDeferrals: readonly (string | null)[],
): string[] {
  const goals: string[] = [];
  for (let index = 0; index < effectiveDeferrals.length; index += 1) {
    const previous = index === 0 ? originalGoal : goals[index - 1];
    goals.push(index === 0 ? originalGoal : (effectiveDeferrals[index - 1] ?? previous));
  }
  return goals;
}
