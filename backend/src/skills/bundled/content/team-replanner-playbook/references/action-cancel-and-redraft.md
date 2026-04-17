# Action Reference: submit_replan (cancel and redraft)

Use `submit_replan(new_tasks=[...], cancel_ids=[...])` when one or more of your direct siblings are stale and must be replaced. Cancelling a sibling cancels the entire node and its subtree automatically. Replacement tasks go into `new_tasks` and are inserted as direct children of this replanner, so downstream work remains blocked until recovery finishes.

## Task/Goal

- A direct sibling is working on invalidated assumptions or the wrong files.
- A shared dependency changed and a sibling's subtree is no longer valid.
- Adding corrective tasks alone would leave stale work running or conflicting.

## Avoid

- Never cancel DONE, FAILED, or CANCELLED tasks; terminal records are immutable.
- Never try to cancel a non-sibling (e.g. a nested task inside a sibling's subtree). Cancel the sibling root instead and let the cascade handle the subtree.
- Do not cancel tasks without confirming they are actually stale.

## Workflow

- Must confirm which direct siblings are actually stale before adding to `cancel_ids`.
- `cancel_ids` accepts only direct siblings of this replanner (same `parent_id`). The replanner cannot cancel itself or the original `request_replan` task.
- Replacement work that logically replaces a cancelled sibling belongs in `new_tasks`. If the replacement itself needs a hierarchy, make it a planner-role task under this replanner and let that planner author its own subtree on the next turn.
- Replacement tasks must not depend on downstream tasks already blocked on this replanner; that creates a recovery-gate dependency cycle.
- Must call `context_changed_since()` before submitting if freshness moved.

## Expected Outcome

- Stale sibling work is replaced cleanly at this layer without duplicate or dangling work, and deeper subtrees are cleaned up by cascade.
