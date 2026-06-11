import { isWorkflowEntityTerminal } from "@eos/contracts";

import type { EntityFieldFile } from "../work-item/context.js";
import { closingAttempt, type IterationState } from "./state.js";

export function iterationFieldFiles(iteration: IterationState): EntityFieldFile[] {
  const files: EntityFieldFile[] = [];
  if (iteration.focus !== null) {
    files.push({ name: "focus.md", content: iteration.focus });
  }
  if (iteration.deferredGoal !== null) {
    files.push({ name: "deferred_goal.md", content: iteration.deferredGoal });
  }
  if (isWorkflowEntityTerminal(iteration.status)) {
    files.push({ name: "outcome.md", content: composeIterationOutcome(iteration) });
  }
  return files;
}

/**
 * §2.16: the iteration outcome is composed at render time from its closing
 * attempt's plan summary and work-item summaries/outcomes - never stored.
 */
export function composeIterationOutcome(iteration: IterationState): string {
  const attempt = closingAttempt(iteration);
  if (!attempt) return "(no attempts)";
  const lines: string[] = [attempt.plan.summary ?? "(no plan summary)"];
  for (const item of attempt.workItems) {
    lines.push("", `- work_item_${item.id} (${item.status}): ${item.summary ?? "(no summary)"}`);
    if (item.outcome !== null) lines.push(`  ${item.outcome}`);
  }
  if (attempt.failReason !== null) {
    lines.push("", `fail_reason: ${attempt.failReason}`);
  }
  return lines.join("\n");
}
