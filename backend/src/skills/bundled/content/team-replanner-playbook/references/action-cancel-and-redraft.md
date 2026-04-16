# Action Reference: submit_replan (cancel and redraft)

Use `submit_replan(new_tasks=[...], cancel_ids=[...])` when sibling tasks are stale and must be replaced. Cancelling a sibling cancels the entire node and its subtree. Replacement tasks may be inserted at the current parent layer or inside surviving sibling subtrees by setting `parent_id`.

## Task/Goal

- Sibling tasks are working on invalidated assumptions or wrong files.
- A shared dependency changed and existing work is no longer valid.
- `submit_replan(new_tasks=[...], cancel_ids=[])` alone would leave stale work running or conflicting.
- Include `expected_projection` when parent-bounded dependency shape or cascade
  impact matters so `submit_replan(...)` can reject a mismatched projection
  before committing.

## Avoid

- Never cancel DONE siblings — they are immutable.
- Do not cancel siblings without confirming they are actually stale.

## Workflow

- Must confirm which siblings are actually stale before adding to `cancel_ids`.
- `cancel_ids` only accepts sibling IDs (same parent level). DONE siblings are immutable and cannot be removed. The replanner cannot cancel itself.
- Replacement tasks in `new_tasks` must cover the work being cancelled and must include `parent_id`.
- Must call `context_changed_since()` before submitting if freshness moved.

## Expected Outcome

- Stale sibling work is replaced cleanly at the current DAG level without duplicate or dangling work.
