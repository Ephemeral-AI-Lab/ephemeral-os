import type { AttemptId, WorkflowEntityRunStatus } from "@eos/contracts";

import type { FocusDeclaration, PlanState } from "../plan/state.js";
import type { WorkItemState } from "../work-item/state.js";

/** One attempt: a plan plus the work items it materialized. */
export interface AttemptState {
  readonly id: AttemptId;
  readonly sequence: number;
  readonly status: WorkflowEntityRunStatus;
  readonly failReason: string | null;
  /** §6 invariant 5: true iff no later plan in the iteration declared. */
  readonly isConsistentWithIterationFocus: boolean;
  readonly plan: PlanState;
  readonly workItems: readonly WorkItemState[];
}

/**
 * The §6 invariant-5 predicate. A declaring attempt is consistent with its
 * own declaration; only attempts before a LATER declaration drift.
 */
export function consistentWithIterationFocus(
  attemptSequence: number,
  declarations: readonly FocusDeclaration[],
): boolean {
  const latest = declarations.at(-1);
  return latest === undefined || attemptSequence >= latest.attemptSequence;
}
