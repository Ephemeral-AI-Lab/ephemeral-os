# Action Reference: declare_blocker

Use `declare_blocker(...)` when a shared dependency is broken and multiple siblings will hit the same error.

## When to choose

- Sibling notes show the same file or symbol causing failures across multiple tasks or descendant subtrees.
- The failure is in shared infrastructure, not the failed task's own scope.
- Fixing the issue independently in each sibling would be redundant.

## Rules

- Must confirm `root_cause_paths` are live with CI tools before declaring.
- Must name specific affected siblings in the reason.
- Must only declare a blocker when two or more siblings are affected or will be affected.
- Must check the Active Blockers section first. If any in-progress blocker overlaps your intended paths, do not declare a new blocker; use `submit_task_plan(new_tasks=[...], deps=[fix_task_id])` instead.
- Must call `context_changed_since()` before submitting if freshness moved.
- Never declare a blocker when only the failed task is affected.
