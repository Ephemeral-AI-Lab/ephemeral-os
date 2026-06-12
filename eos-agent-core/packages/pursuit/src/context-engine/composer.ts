import type {
  InitialUserMessage,
  PlannerContextInput,
  WorkerContextInput,
  PursuitContextAttempt,
  PursuitContextWorkItem,
} from "@eos/contracts";

/**
 * The one composer seam (§2.11): called after commit, before
 * `port.launch`; its result IS the launch's complete ordered initial
 * messages - replace, never merge. A rejection is a compose failure and
 * synthesizes a failed settlement (§2.14). Subprocess composers inherit
 * the launch's pursuit signal.
 */
export type ComposeLaunchContext = (
  agentName: string,
  input: PlannerContextInput | WorkerContextInput,
  signal?: AbortSignal,
) => Promise<InitialUserMessage[]>;

/**
 * The §2.13 default policy as a pure function - the in-package fallback
 * for engine-free suites that bypass runtime profiles. Profile-based
 * planner/worker runtime launches name a context script instead.
 */
export const defaultComposeLaunchContext: ComposeLaunchContext = (
  _agentName,
  input,
) =>
  Promise.resolve(
    input.kind === "planner" ? plannerMessages(input) : workerMessages(input),
  );

function user(text: string): InitialUserMessage {
  return { role: "user", content: [{ type: "text", text }] };
}

function plannerMessages(input: PlannerContextInput): InitialUserMessage[] {
  const pursuit = input.pursuit_context.pursuit;
  const leg = pursuit.legs.find(
    (candidate) => candidate.id === input.current.leg_id,
  );
  const messages = [user(`# Current goal\n${goalForLeg(input)}`)];

  const standingLegGoal = leg?.focus ?? null;
  if (!leg || standingLegGoal === null) {
    messages.push(
      user(
        "Declare this leg's focus (`leg_goal`): the slice of the " +
          "current goal this leg will complete. Optionally declare " +
          "`next_leg_goal` for the remainder. Plan the work items for that " +
          "focus and submit via submit_planner_outcome.",
      ),
    );
    return messages;
  }

  messages.push(user(`# Leg focus\n${standingLegGoal}`));
  // Only attempts consistent with the standing focus, fully expanded; the
  // standing next_leg_goal and superseded attempts are deliberately omitted
  // (§2.5) - a refocus would re-peel from an unchanged pursuit goal.
  const failed = leg.attempts.filter(
    (attempt) =>
      attempt.is_consistent_with_leg_goal && attempt.status === "Failed",
  );
  for (const attempt of failed) {
    messages.push(user(failedAttemptReport(attempt)));
  }
  messages.push(
    user(
      "Re-plan work items within the standing focus, or declare a new " +
        "`leg_goal` to refocus - refocusing resets BOTH " +
        "`leg_goal` and `next_leg_goal`. Submit via " +
        "submit_planner_outcome.",
    ),
  );
  return messages;
}

function goalForLeg(input: PlannerContextInput | WorkerContextInput): string {
  const pursuit = input.pursuit_context.pursuit;
  const index = pursuit.legs.findIndex(
    (leg) => leg.id === input.current.leg_id,
  );
  let goal = pursuit.goal;
  for (let cursor = 1; cursor <= index; cursor += 1) {
    goal = pursuit.legs[cursor - 1]?.next_leg_goal ?? goal;
  }
  return goal;
}

function failedAttemptReport(attempt: PursuitContextAttempt): string {
  const lines = [`# Failed attempt ${String(attempt.sequence)}`];
  if (attempt.failure_reasons !== null) lines.push(`failure_reasons: ${attempt.failure_reasons}`);
  if (attempt.plan.summary !== null) lines.push(`plan summary: ${attempt.plan.summary}`);
  for (const item of attempt.work_items) {
    lines.push(
      `- work_item ${item.id} [${item.status}] (${item.agent_name}): ${item.description}`,
    );
    if (item.summary !== null) lines.push(`  summary: ${item.summary}`);
    if (item.outcome !== null) lines.push(`  outcome: ${item.outcome}`);
  }
  return lines.join("\n");
}

function workerMessages(input: WorkerContextInput): InitialUserMessage[] {
  const pursuit = input.pursuit_context.pursuit;
  const leg = pursuit.legs.find(
    (candidate) => candidate.id === input.current.leg_id,
  );
  const items =
    leg?.attempts.flatMap((attempt) => attempt.work_items) ?? [];
  const item = items.find((candidate) => candidate.id === input.current.work_item_id);

  const messages = [user(`# Leg focus\n${leg?.focus ?? ""}`)];
  const dependencies = (item?.needs ?? [])
    .map((id) => items.find((candidate) => candidate.id === id))
    .filter((dependency): dependency is PursuitContextWorkItem => dependency !== undefined);
  if (dependencies.length > 0) {
    messages.push(user(dependencyReport(dependencies)));
  }
  messages.push(user(`# Work item description\n${item?.description ?? ""}`));
  messages.push(user(`# Work item spec\n${item?.spec ?? ""}`));
  messages.push(
    user("Complete this work item, then submit via submit_worker_outcome."),
  );
  return messages;
}

function dependencyReport(dependencies: readonly PursuitContextWorkItem[]): string {
  const lines = ["# Dependency outcomes"];
  for (const dependency of dependencies) {
    lines.push(
      `- work_item ${dependency.id} [${dependency.status}]: ${dependency.summary ?? "(no summary)"}`,
    );
    if (dependency.outcome !== null) lines.push(`  ${dependency.outcome}`);
  }
  return lines.join("\n");
}
