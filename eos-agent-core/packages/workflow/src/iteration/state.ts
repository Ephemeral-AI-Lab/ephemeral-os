import type { IterationId, WorkflowEntityRunStatus } from "@eos/contracts";

import type { AttemptState } from "../attempt/state.js";
import type { FocusDeclaration } from "../plan/state.js";

/** One iteration with its derived focus views (§8 derivation table). */
export interface IterationState {
  readonly id: IterationId;
  readonly sequence: number;
  readonly origin: "initial" | "deferred_goal";
  readonly maxAttempts: number;
  readonly status: WorkflowEntityRunStatus;
  /** The goal in effect DURING this iteration (head of the deferral chain). */
  readonly goal: string;
  /** Latest declaration views; null means no declaration yet. */
  readonly focus: string | null;
  readonly deferredGoal: string | null;
  readonly attempts: readonly AttemptState[];
}

/**
 * The ordered declaration view: plans with a non-null `declared_focus`,
 * in attempt order. Append-only by construction - declarations are never
 * mutated, only superseded by a later row.
 */
export function orderedDeclarations(
  attempts: readonly {
    id: AttemptState["id"];
    sequence: number;
    plan: { declaredFocus: string | null; declaredDeferredGoal: string | null };
  }[],
): FocusDeclaration[] {
  const declarations: FocusDeclaration[] = [];
  for (const attempt of attempts) {
    if (attempt.plan.declaredFocus === null) continue;
    declarations.push({
      focus: attempt.plan.declaredFocus,
      deferredGoal: attempt.plan.declaredDeferredGoal,
      attemptId: attempt.id,
      attemptSequence: attempt.sequence,
    });
  }
  return declarations;
}

/** Closure outcomes derive from the last attempt (§2.16). */
export function closingAttempt(
  iteration: Pick<IterationState, "attempts">,
): AttemptState | undefined {
  return iteration.attempts.at(-1);
}
