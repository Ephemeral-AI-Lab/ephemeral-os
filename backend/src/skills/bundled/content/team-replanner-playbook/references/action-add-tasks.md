# Action Reference: submit_task_plan (add corrective tasks)

Use `submit_task_plan(new_tasks=[...])` when the plan structure is sound but more work is needed. Existing siblings continue running.

## Task/Goal

- An isolated task failed and needs a corrective retry or follow-up.
- A transient error needs one retry with the same or narrower scope.
- Follow-up validation is needed after a fix lands.
- Include `expected_graph={"task_id": ["dep_id", ...]}` when sibling dependency
  shape matters so `submit_task_plan(...)` can reject a mismatched projection
  before committing.

## Avoid

- Never submit corrective tasks without reading sibling notes first.
- Do not add tasks that duplicate work already covered by existing siblings.

## Workflow

- Must confirm owner paths live with CI before submitting.
- Must read sibling notes before deciding corrective scope.
- Each new task must have: `id`, `name` (agent), `spec`, `deps`, `scope_paths`.
- Each `spec` must use these sections in order: `Goal`, `Environment`, `Scope`, `Context`, `Acceptance Criteria`.
- Must call `context_changed_since()` before submitting if freshness moved.
- For layered failures, emit a two-phase corrective plan (see Hard Rule 10 in main playbook).
- Corrective developer tasks must instruct the developer to run `ci_diagnostics(file_path)` on affected files first.

## Expected Outcome

- The replanner adds only the missing corrective work and leaves still-valid siblings running.
