import type {
  InitialUserMessage,
  PlannerContextInput,
  WorkerContextInput,
  WorkflowContextAttempt,
  WorkflowContextWorkItem,
} from "@eos/contracts";

/**
 * The one composer seam (§2.11): called after commit, before
 * `port.launch`; its result IS the launch's complete ordered initial
 * messages - replace, never merge. A rejection is a compose failure and
 * synthesizes a failed settlement (§2.14). Subprocess composers inherit
 * the launch's workflow signal.
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
  const workflow = input.workflow_context.workflow;
  const iteration = workflow.iterations.find(
    (candidate) => candidate.id === input.current.iteration_id,
  );
  const messages = [user(`# Current goal\n${goalForIteration(input)}`)];

  const standingFocus = iteration?.focus ?? null;
  if (!iteration || standingFocus === null) {
    messages.push(
      user(
        "Declare this iteration's focus (`iteration_focus`): the slice of the " +
          "current goal this iteration will complete. Optionally declare " +
          "`deferred_goal` for the remainder. Plan the work items for that " +
          "focus and submit via submit_planner_outcome.",
      ),
    );
    return messages;
  }

  messages.push(user(`# Iteration focus\n${standingFocus}`));
  // Only attempts consistent with the standing focus, fully expanded; the
  // standing deferred_goal and superseded attempts are deliberately omitted
  // (§2.5) - a refocus would re-peel from an unchanged workflow goal.
  const failed = iteration.attempts.filter(
    (attempt) =>
      attempt.is_consistent_with_iteration_focus && attempt.status === "Failed",
  );
  for (const attempt of failed) {
    messages.push(user(failedAttemptReport(attempt)));
  }
  messages.push(
    user(
      "Re-plan work items within the standing focus, or declare a new " +
        "`iteration_focus` to refocus - refocusing resets BOTH " +
        "`iteration_focus` and `deferred_goal`. Submit via " +
        "submit_planner_outcome.",
    ),
  );
  return messages;
}

function goalForIteration(input: PlannerContextInput | WorkerContextInput): string {
  const workflow = input.workflow_context.workflow;
  const index = workflow.iterations.findIndex(
    (iteration) => iteration.id === input.current.iteration_id,
  );
  let goal = workflow.goal;
  for (let cursor = 1; cursor <= index; cursor += 1) {
    goal = workflow.iterations[cursor - 1]?.deferred_goal ?? goal;
  }
  return goal;
}

function failedAttemptReport(attempt: WorkflowContextAttempt): string {
  const lines = [`# Failed attempt ${String(attempt.sequence)}`];
  if (attempt.fail_reason !== null) lines.push(`fail_reason: ${attempt.fail_reason}`);
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
  const workflow = input.workflow_context.workflow;
  const iteration = workflow.iterations.find(
    (candidate) => candidate.id === input.current.iteration_id,
  );
  const items =
    iteration?.attempts.flatMap((attempt) => attempt.work_items) ?? [];
  const item = items.find((candidate) => candidate.id === input.current.work_item_id);

  const messages = [user(`# Iteration focus\n${iteration?.focus ?? ""}`)];
  const dependencies = (item?.needs ?? [])
    .map((id) => items.find((candidate) => candidate.id === id))
    .filter((dependency): dependency is WorkflowContextWorkItem => dependency !== undefined);
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

function dependencyReport(dependencies: readonly WorkflowContextWorkItem[]): string {
  const lines = ["# Dependency outcomes"];
  for (const dependency of dependencies) {
    lines.push(
      `- work_item ${dependency.id} [${dependency.status}]: ${dependency.summary ?? "(no summary)"}`,
    );
    if (dependency.outcome !== null) lines.push(`  ${dependency.outcome}`);
  }
  return lines.join("\n");
}
