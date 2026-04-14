# Action Reference: cancel_and_redraft

Use `cancel_and_redraft(...)` when sibling tasks are stale and must be replaced. Cancelling a sibling cancels the entire node and its subtree. Replacement tasks are planned at the current DAG level only.

## When to choose

- One or more siblings are working on the wrong files, wrong ordering, wrong approach, or overlapping ownership.
- A completed task invalidated the premise of specific remaining siblings.
- `add_tasks(...)` alone would leave stale work running or conflicting.

## Cancellation semantics

- Cancel atomic or expandable sibling nodes; both are valid.
- Cancelled tasks can be running or pending.
- Scope the cancellation to what is actually stale. Never cancel more than necessary.

## Replacement task rules

- Must plan replacements at the current DAG level only. Never decompose into subtrees yourself; assign `team_planner` when the replacement still needs expansion.
- Must ensure replacement tasks do not depend on any cancelled task.
- Must include failure context from the original plan so the replacement does not repeat the same mistake.

## Signals

- The failure is a wrong-owner or wrong-decomposition problem rather than an isolated bug or transient runtime.
- Adding corrective tasks without cancelling would create conflicting edits on the same files.
- Must read sibling and descendant notes via `read_sibling_notes()` before deciding which nodes are stale.

## Rules

- Must only cancel nodes that are genuinely stale. Never cancel a sibling that completed valid work.
- Must call `context_changed_since()` before submitting if freshness moved.
