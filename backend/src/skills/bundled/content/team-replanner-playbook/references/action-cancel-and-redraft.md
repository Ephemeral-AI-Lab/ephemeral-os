# Action Reference: submit_replan (cancel and redraft)

Use `submit_replan(new_tasks=[...], cancel_ids=[...])` when not-completed tasks in your allowed parent projection are stale and must be replaced. Cancelling a task cancels the entire node and its subtree. Replacement tasks may be inserted under the current replanner, at the current parent layer, or inside surviving sibling subtrees by setting `parent_id`.

## Task/Goal

- Not-completed tasks are working on invalidated assumptions or wrong files.
- A shared dependency changed and existing work is no longer valid.
- `submit_replan(new_tasks=[...], cancel_ids=[])` alone would leave stale work running or conflicting.
## Avoid

- Never cancel DONE, FAILED, or CANCELLED tasks; terminal records are immutable.
- Do not cancel tasks without confirming they are actually stale.

## Workflow

- Must confirm which not-completed tasks are actually stale before adding to `cancel_ids`.
- `cancel_ids` accepts not-completed task IDs in the allowed parent projection. DONE, FAILED, and CANCELLED tasks are immutable. The replanner cannot cancel itself.
- Replacement tasks in `new_tasks` must cover the work being cancelled and must include `parent_id`.
- Must call `context_changed_since()` before submitting if freshness moved.

## Expected Outcome

- Stale work is replaced cleanly within the allowed parent projection without duplicate or dangling work.
