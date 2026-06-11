import type {
  AgentRunId,
  AttemptId,
  PlanId,
  WorkflowEntityRunStatus,
} from "@eos/contracts";

/** The planning-act record: status, summary, and the declared pair. */
export interface PlanState {
  readonly id: PlanId;
  readonly attemptId: AttemptId;
  readonly agentRunId: AgentRunId | null;
  readonly status: WorkflowEntityRunStatus;
  /** Null = this plan kept the standing declaration. */
  readonly declaredFocus: string | null;
  readonly declaredDeferredGoal: string | null;
  readonly summary: string | null;
}

/**
 * One append-only declaration: a peel of `current_goal` recorded on the
 * submitting plan row. The iteration's focus, deferred goal, and archives
 * are all views over the ordered list of these.
 */
export interface FocusDeclaration {
  readonly focus: string;
  readonly deferredGoal: string | null;
  readonly attemptId: AttemptId;
  readonly attemptSequence: number;
}
