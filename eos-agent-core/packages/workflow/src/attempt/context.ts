import type { EntityFieldFile } from "../work-item/context.js";
import type { AttemptState } from "./state.js";

/** `fail_reason.md` exists on failed attempts only. */
export function attemptFieldFiles(attempt: AttemptState): EntityFieldFile[] {
  return attempt.status === "Failed" && attempt.failReason !== null
    ? [{ name: "fail_reason.md", content: attempt.failReason }]
    : [];
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
