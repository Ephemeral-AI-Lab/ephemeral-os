// Shared helper for workflow context scripts: loaded by planner.cjs /
// worker.cjs, never spawned directly. Derives every convenience variable
// from the WorkflowContextSnapshot the runtime pipes in on stdin.
function create_variable_reference_map(ctx) {
  const workflow = ctx.workflow_context.workflow;
  const current_iteration = workflow.iterations.find(
    (i) => i.id === ctx.current.iteration_id,
  ) ?? null;
  const previous_iteration =
    workflow.iterations
      .filter(
        (i) => current_iteration && i.sequence < current_iteration.sequence,
      )
      .at(-1) ?? null;
  const all_attempts = current_iteration?.attempts ?? [];
  const current_attempt = current_iteration?.attempts.find(
    (a) => a.id === ctx.current.attempt_id,
  ) ?? null;
  const previous_attempt =
    all_attempts
      .filter((a) => current_attempt && a.sequence < current_attempt.sequence)
      .at(-1) ?? null;
  const last_attempt = all_attempts.at(-1) ?? null;
  const all_work_items = workflow.iterations.flatMap((iteration) =>
    iteration.attempts.flatMap((attempt) => attempt.work_items),
  );
  const current_work_item =
    "work_item_id" in ctx.current
      ? all_work_items.find((item) => item.id === ctx.current.work_item_id) ?? null
      : null;
  const dependencies = current_work_item
    ? current_work_item.needs.map(
        (id) => all_work_items.find((item) => item.id === id) ?? { id },
      )
    : [];

  const attempt_outcome = (attempt) =>
    attempt === null
      ? null
      : {
          attempt_id: attempt.id,
          status: attempt.status,
          fail_reason: attempt.fail_reason,
          plan_summary: attempt.plan.summary,
          is_consistent_with_iteration_focus: attempt.is_consistent_with_iteration_focus,
          plan_context_path: attempt.plan.context_path,
          work_items: attempt.work_items.map((item) => ({
            id: item.id,
            agent_name: item.agent_name,
            description: item.description,
            status: item.status,
            summary: item.summary,
            outcome: item.outcome,
            context_path: item.context_path,
          })),
        };
  const iteration_outcome = (iteration) =>
    iteration === null ? null : attempt_outcome(iteration.attempts.at(-1) ?? null);

  return {
    kind: ctx.kind,

    workflow_id: workflow.id,
    workflow_status: workflow.status,
    workflow_goal: workflow.current_goal,
    original_workflow_goal: workflow.original_goal,
    current_workflow_goal: workflow.current_goal,
    workflow_context_path: workflow.context_path,

    current_iteration_id: current_iteration?.id ?? null,
    current_iteration_sequence: current_iteration?.sequence ?? null,
    current_iteration_origin: current_iteration?.origin ?? null,
    current_iteration_status: current_iteration?.status ?? null,
    current_iteration_focus: current_iteration?.focus ?? null,
    current_iteration_deferred_goal: current_iteration?.deferred_goal ?? null,
    current_iteration_max_attempts: current_iteration?.max_attempts ?? null,
    current_iteration_context_path: current_iteration?.context_path ?? null,
    current_iteration_outcome: iteration_outcome(current_iteration),

    previous_iteration_id: previous_iteration?.id ?? null,
    previous_iteration_sequence: previous_iteration?.sequence ?? null,
    previous_iteration_status: previous_iteration?.status ?? null,
    previous_iteration_focus: previous_iteration?.focus ?? null,
    previous_iteration_deferred_goal: previous_iteration?.deferred_goal ?? null,
    previous_iteration_context_path: previous_iteration?.context_path ?? null,
    previous_iteration_outcome: iteration_outcome(previous_iteration),

    current_attempt_id: current_attempt?.id ?? null,
    current_attempt_sequence: current_attempt?.sequence ?? null,
    current_attempt_status: current_attempt?.status ?? null,
    current_attempt_fail_reason: current_attempt?.fail_reason ?? null,
    current_attempt_is_consistent_with_iteration_focus:
      current_attempt?.is_consistent_with_iteration_focus ?? null,
    current_attempt_context_path: current_attempt?.context_path ?? null,
    current_attempt_outcome: attempt_outcome(current_attempt),
    current_attempt_work_items: current_attempt?.work_items ?? [],

    previous_attempt_id: previous_attempt?.id ?? null,
    previous_attempt_sequence: previous_attempt?.sequence ?? null,
    previous_attempt_status: previous_attempt?.status ?? null,
    previous_attempt_fail_reason: previous_attempt?.fail_reason ?? null,
    previous_attempt_is_consistent_with_iteration_focus:
      previous_attempt?.is_consistent_with_iteration_focus ?? null,
    previous_attempt_context_path: previous_attempt?.context_path ?? null,
    previous_attempt_outcome: attempt_outcome(previous_attempt),

    last_attempt_id: last_attempt?.id ?? null,
    last_attempt_status: last_attempt?.status ?? null,
    last_attempt_fail_reason: last_attempt?.fail_reason ?? null,
    last_attempt_context_path: last_attempt?.context_path ?? null,
    last_attempt_outcome: attempt_outcome(last_attempt),

    attempts_consistent_with_iteration_focus: all_attempts.filter(
      (attempt) => attempt.is_consistent_with_iteration_focus,
    ),
    attempts_not_consistent_with_iteration_focus: all_attempts.filter(
      (attempt) => !attempt.is_consistent_with_iteration_focus,
    ),
    failed_attempts: all_attempts.filter((attempt) => attempt.status === "Failed"),
    cancelled_attempts: all_attempts.filter((attempt) => attempt.status === "Cancelled"),

    current_plan_id: current_attempt?.plan.id ?? null,
    current_plan_status: current_attempt?.plan.status ?? null,
    current_plan_summary: current_attempt?.plan.summary ?? null,
    current_plan_declared_focus: current_attempt?.plan.declared_focus ?? null,
    current_plan_declared_deferred_goal:
      current_attempt?.plan.declared_deferred_goal ?? null,
    current_plan_context_path: current_attempt?.plan.context_path ?? null,

    work_item_id: current_work_item?.id ?? null,
    work_item_agent_name: current_work_item?.agent_name ?? null,
    work_item_description: current_work_item?.description ?? null,
    work_item_spec: current_work_item?.spec ?? null,
    work_item_status: current_work_item?.status ?? null,
    work_item_summary: current_work_item?.summary ?? null,
    work_item_outcome: current_work_item?.outcome ?? null,
    work_item_needs: current_work_item?.needs ?? [],
    work_item_context_path: current_work_item?.context_path ?? null,
    dependency_work_items: dependencies,
    dependency_outcomes: dependencies.map((item) => ({
      id: item.id,
      description: item.description ?? null,
      status: item.status ?? "Unknown",
      summary: item.summary ?? null,
      outcome: item.outcome ?? null,
    })),
  };
}

module.exports = { create_variable_reference_map };
