# Action Reference: submit_task_plan (cancel and redraft)

Use `submit_task_plan(new_tasks=[...], remove_tasks=[...])` when sibling tasks are stale and must be replaced. Cancelling a sibling cancels the entire node and its subtree. Replacement tasks are planned at the current DAG level only.

## Task/Goal

- Sibling tasks are working on invalidated assumptions or wrong files.
- A shared dependency changed and existing work is no longer valid.
- `submit_task_plan(new_tasks=[...])` alone would leave stale work running or conflicting.
- Include `expected_graph={"task_id": ["dep_id", ...]}` when sibling dependency
  shape matters so `submit_task_plan(...)` can reject a mismatched projection
  before committing.

## Avoid

- Never cancel DONE siblings — they are immutable.
- Do not cancel siblings without confirming they are actually stale.

## Workflow

- Must confirm which siblings are actually stale before adding to `remove_tasks`.
- `remove_tasks` only accepts sibling IDs (same parent level). DONE siblings are immutable and cannot be removed.
- Replacement tasks in `new_tasks` must cover the work being cancelled.
- Must call `context_changed_since()` before submitting if freshness moved.

## Expected Outcome

- Stale sibling work is replaced cleanly at the current DAG level without duplicate or dangling work.
