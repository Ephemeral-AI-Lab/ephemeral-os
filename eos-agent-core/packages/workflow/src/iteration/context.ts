import { composeAttemptOutcome } from "../attempt/context.js";
import type { EntityFieldFile } from "../work-item/context.js";
import { closingAttempt, type IterationState } from "./state.js";

/**
 * Iteration field files: the latest declaration pair plus `outcome.md`
 * once the iteration closes `Success` or `Failed`. Cancelled iterations
 * carry no business outcome (§2.6).
 */
export function iterationFieldFiles(iteration: IterationState): EntityFieldFile[] {
  const files: EntityFieldFile[] = [];
  if (iteration.focus !== null) {
    files.push({ name: "focus.md", content: iteration.focus });
  }
  if (iteration.deferredGoal !== null) {
    files.push({ name: "deferred_goal.md", content: iteration.deferredGoal });
  }
  if (iteration.status === "Success" || iteration.status === "Failed") {
    files.push({ name: "outcome.md", content: composeIterationOutcome(iteration) });
  }
  return files;
}

/** §5.2: the iteration outcome IS the closing attempt's derived outcome. */
export function composeIterationOutcome(iteration: IterationState): string {
  const attempt = closingAttempt(iteration);
  return attempt ? composeAttemptOutcome(attempt) : "(no attempts)";
}
