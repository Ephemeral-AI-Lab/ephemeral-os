# Action Reference: cancel_and_redraft

Use `cancel_and_redraft(...)` when sibling tasks are stale and need replacement. Cancelling a sibling cancels the entire node and its subtree. Replacement tasks are planned at the current DAG level only — never plan into subtrees yourself.

## When to choose

- One or more siblings are working on the wrong files, wrong ordering, or wrong approach — and `add_tasks` alone would leave stale work running or conflicting.
- A completed task's changes invalidated the premise of specific remaining siblings.
- Scope boundaries between tasks were drawn incorrectly (overlapping ownership, missing surfaces) and affected tasks need to be redrawn.
- A sibling's subtree is pursuing a dead-end approach that will waste budget if allowed to continue.

The key distinction from `add_tasks`: use `cancel_and_redraft` when stale tasks must be **stopped**, not just supplemented. If existing siblings can continue safely alongside new work, prefer `add_tasks`.

## Cancellation semantics

Cancelling a task ID cancels the **entire node and its subtree**. You do not cancel individual child tasks within a subtree — cancel the sibling-level node and everything underneath collapses.

- Cancel atomic tasks (leaf nodes) or expandable tasks (nodes with children) — both are valid.
- Cancelled tasks can be running or pending — both are valid targets.
- You can cancel one sibling or many — scope the cancellation to what is actually stale.

## Replacement task rules

Replacement tasks in `add_tasks` are inserted at the **current DAG level** — the same level as the cancelled siblings. This is the same contract as `submit_plan`.

- Must plan for the current level only. Never plan a subtree decomposition inside a replacement task.
- If a replacement task needs further decomposition, assign it to `team_planner` so it becomes an expandable node that gets planned in a subsequent round.
- If a replacement task is atomic (single developer fix), assign it to `developer`.

## Signals

"Siblings" here means sibling tasks **and their descendant subtrees**.

- The failure reason is "wrong owner file" or "wrong decomposition" rather than a bug or transient error.
- A sibling is actively editing a file that should belong to a different task's scope.
- Adding corrective tasks without cancelling would create conflicting edits on the same files.
- The validator packet reveals that specific plan assumptions about the codebase were incorrect.

Must read sibling and descendant notes via `read_notes(scope="siblings")` to determine which nodes are stale and which are still valid.

## Examples

### Cancel one mis-scoped atomic sibling, replace with corrected task
```json
{
  "add_tasks": [
    {
      "id": "fix-compat-v2",
      "task": "Original task targeted pkg/helpers.py but the actual compat surface is pkg/_compat.py. Fix the export surface there. Previous error: ImportError on _old_compat.",
      "agent": "developer",
      "deps": [],
      "scope_paths": ["pkg/_compat.py"]
    }
  ],
  "cancel_ids": ["fix-compat-v1"]
}
```

### Cancel an expandable sibling, replace with a replanned expandable node
```json
{
  "add_tasks": [
    {
      "id": "compat-lane-v2",
      "task": "Replan the compat lane. Original decomposition split by consumer file, but the compat surface is a single module (pkg/_compat.py) exporting to 4 consumers. Plan a fix-then-verify approach centered on the export surface.",
      "agent": "team_planner",
      "deps": [],
      "scope_paths": ["pkg/_compat.py", "pkg/io.py", "pkg/parser.py", "pkg/cli.py"]
    }
  ],
  "cancel_ids": ["compat-lane-v1"]
}
```

### Cancel multiple stale siblings, replace with fresh decomposition at current level
```json
{
  "add_tasks": [
    {
      "id": "fix-shared-compat",
      "task": "Fix the shared _compat export surface in pkg/_compat.py. All 4 consumer fixes were wrong because they targeted consumers instead of the source.",
      "agent": "developer",
      "deps": [],
      "scope_paths": ["pkg/_compat.py"]
    },
    {
      "id": "verify-all-consumers",
      "task": "Verify all 4 consumer imports resolve after the shared fix. Run pytest pkg/tests/ -x -q.",
      "agent": "developer",
      "deps": ["fix-shared-compat"],
      "scope_paths": ["pkg/tests/"],
      "cascade_policy": "continue"
    }
  ],
  "cancel_ids": ["fix-io-v1", "fix-parser-v1", "fix-cli-v1"]
}
```

## Rules

- Must only cancel nodes that are genuinely stale. Never cancel a sibling that completed successfully with valid work.
- Must plan replacement tasks at the current DAG level only. Never decompose into subtrees — assign `team_planner` for tasks that need expansion.
- Must ensure replacement tasks do not depend on any cancelled task.
- Must include failure context from the original plan in new task briefings so the agent does not repeat the same mistakes.
- Must call `context_changed_since()` before submitting if freshness moved.
- Never cancel more than necessary — prefer the narrowest scope that fixes the problem.
