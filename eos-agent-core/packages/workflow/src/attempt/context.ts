import type { EntityFieldFile } from "../work-item/context.js";
import type { AttemptState } from "./state.js";

/**
 * Attempt-owned field files (§4): the accepted planner summary as
 * `plan_summary.md`, `fail_reason.md` on failed attempts, and the derived
 * `outcome.md` once the attempt closes `Success` or `Failed`.
 */
export function attemptFieldFiles(attempt: AttemptState): EntityFieldFile[] {
  const files: EntityFieldFile[] = [];
  if (attempt.plan.summary !== null) {
    files.push({ name: "plan_summary.md", content: attempt.plan.summary });
  }
  if (attempt.status === "Failed" && attempt.failReason !== null) {
    files.push({ name: "fail_reason.md", content: attempt.failReason });
  }
  if (attempt.status === "Success" || attempt.status === "Failed") {
    files.push({ name: "outcome.md", content: composeAttemptOutcome(attempt) });
  }
  return files;
}

/**
 * §5.1: the attempt outcome is a render-time projection over the work
 * items in planner order - statuses and worker summaries only. Work-item
 * `outcome.md` content and the attempt fail reason stay separate facts.
 */
export function composeAttemptOutcome(attempt: AttemptState): string {
  if (attempt.workItems.length === 0) {
    return "# Attempt outcome\n(no work items)";
  }
  const rows = attempt.workItems.map(
    (item) =>
      `- work_item_${item.id} [${item.status}]: ${item.summary ?? "(no summary)"}`,
  );
  return ["# Attempt outcome", ...rows].join("\n");
}

/**
 * The superseded declaration files riding a drifted attempt (§2.8): only
 * the attempt whose plan made the now-superseded declaration carries them.
 */
export function archivedDeclarationFiles(attempt: AttemptState): EntityFieldFile[] {
  if (attempt.plan.declaredFocus === null) return [];
  const files: EntityFieldFile[] = [
    { name: "focus.md", content: attempt.plan.declaredFocus },
  ];
  if (attempt.plan.declaredDeferredGoal !== null) {
    files.push({ name: "deferred_goal.md", content: attempt.plan.declaredDeferredGoal });
  }
  return files;
}
