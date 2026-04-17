# Action Reference: submit_replan (add corrective tasks)

Use `submit_replan(new_tasks=[...], cancel_ids=[])` when the plan structure is sound but more work is needed. Existing siblings continue running while downstream work remains blocked on this replanner until its new child tasks complete.

## Task/Goal

- An isolated task failed and needs corrective follow-up.
- A transient error needs a new corrective task with the same or narrower scope.
- Follow-up validation is needed after a fix lands.

## Avoid

- Never submit corrective tasks without reading sibling notes first.
- Do not add tasks that duplicate work already covered by existing siblings.

## Workflow

- Must confirm owner paths live with CI before submitting.
- Must read sibling notes before deciding corrective scope.
- Each new task must have: `id`, `name` (agent), `spec`, `deps`, `scope_paths`. Do not set `parent_id`; every new task is inserted as a direct child of this replanner.
- Put all corrective work in `new_tasks`.
- `deps` on existing tasks must target tasks whose status is `done`, `ready`, or `pending` and that do not already depend on this replanner or the original failed task. Do NOT depend on downstream tasks blocked on this replanner, or on tasks in `request_replan`, `running`, `expanded`, `failed`, or `cancelled` — these are either cyclic, transitioning, transient, or detached and the scheduler will never promote a dependent past them.
- Each `spec` must use these sections in order: `Goal`, `Environment`, `Scope`, `Context`, `Acceptance Criteria`.
- Must call `context_changed_since()` before submitting if freshness moved.
- For layered failures, emit a two-phase corrective plan (see Hard Rule 10 in main playbook).
- Corrective developer tasks must instruct the developer to run `ci_diagnostics(file_path)` on affected files first.

## Expected Outcome

- The replanner adds only the missing corrective work and leaves still-valid siblings running.
