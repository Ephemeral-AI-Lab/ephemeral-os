import type { EntityFieldFile } from "../work-item/context.js";
import type { AttemptState } from "./state.js";

/**
 * Attempt-owned field files (§4): the accepted planner summary as
 * `plan_summary.md`, `failure_reasons.md` on failed attempts, and the derived
 * `outcome.md` once the attempt closes `Success` or `Failed`.
 */
export function attemptFieldFiles(attempt: AttemptState): EntityFieldFile[] {
  const files: EntityFieldFile[] = [];
  if (attempt.plan.summary !== null) {
    files.push({ name: "plan_summary.md", content: attempt.plan.summary });
  }
  if (attempt.status === "Failed" && attempt.failureReasons.length > 0) {
    files.push({
      name: "failure_reasons.md",
      content: attempt.failureReasons.map((reason) => `- ${reason}`).join("\n"),
    });
  }
  if (attempt.status === "Success" || attempt.status === "Failed") {
    files.push({ name: "outcome.md", content: composeAttemptOutcome(attempt) });
  }
  return files;
}

/**
 * §5.1: the attempt outcome is a render-time projection over the work
 * items in planner order - statuses and worker summaries only. Work-item
 * `outcome.md` content and attempt failure reasons stay separate facts.
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
export function supersededDeclarationFiles(attempt: AttemptState): EntityFieldFile[] {
  if (attempt.plan.declaredLegGoal === null) return [];
  const files: EntityFieldFile[] = [
    { name: "leg_goal.md", content: attempt.plan.declaredLegGoal },
  ];
  if (attempt.plan.declaredNextLegGoal !== null) {
    files.push({ name: "next_leg_goal.md", content: attempt.plan.declaredNextLegGoal });
  }
  return files;
}
