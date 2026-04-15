# Action Reference: add_tasks

Use `add_tasks(...)` when the plan structure is sound but more work is needed. Siblings continue running.

## When to choose

- Must read sibling and descendant notes via `read_task_note(scope="sibling", )` before choosing this action so you confirm the failure is truly isolated.
- Choose this for isolated failures, transient retries, partial progress that left follow-up work, or a newly discovered dependency task.

## Task shape

- Retry tasks must restate the original goal, append failure context, add any new `deps`, and adjust `scope_paths` when live evidence changed the owner surface.
- Follow-up tasks should target the remaining gap, not redo the full task.
- Corrective developer tasks must instruct the agent to start with systematic diagnosis: run `ci_diagnostics(file_path)` on every file in `scope_paths` and on the files named in the validator's error evidence, identify all errors, then fix them before running verification. Include the exact error snippet from the validator packet so the developer does not re-investigate from scratch.

## Layered failure pattern

When the failure is layered (visible errors mask deeper functional failures — see Workflow step 6 and Hard Rule 10), emit a two-phase corrective chain:

1. **Phase 1 (corrective)**: Developer task fixing the visible errors (imports, bridges, init). Validator runs the **full original test target list**, not just the import-level subset. Set `cascade_policy: "continue"` so Phase 2 can proceed.
2. **Phase 2 (carry-forward)**: Developer task with `deps` on Phase 1's validator. Restates the full original test targets and scope paths from the failed task. Briefing explains that imports are now fixed and the developer must address remaining functional failures. Validator runs the full original test target list again.

This prevents the common failure mode where fixing imports is treated as "done" while functional behavior bugs (`TypeError`, missing `raise`, wrong return type) are silently dropped.

## Rules

- Must plan new tasks at the current DAG level only. Never decompose into subtrees; assign `team_planner` for work that still needs expansion.
- Must pair each new developer task with a validator task (`cascade_policy: "continue"`).
- Must include exact failing test ids and an error snippet from the validator packet in the new task briefing.
- Must include failure context so the agent does not repeat the same approach.
- Never bundle unrelated fixes into one task.
- Never omit `scope_paths`.
- When emitting a carry-forward task (Phase 2), must copy ALL test target ids from the original failed task's briefing — never narrow to only the errors the validator surfaced.
