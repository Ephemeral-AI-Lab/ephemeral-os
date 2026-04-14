# Action Reference: add_tasks

Use `add_tasks(...)` when the plan structure is sound but more work is needed. Siblings continue running.

## When to choose

- Must read sibling and descendant notes via `read_sibling_notes()` before choosing this action so you confirm the failure is truly isolated.
- Choose this for isolated failures, transient retries, partial progress that left follow-up work, or a newly discovered dependency task.

## Task shape

- Retry tasks must restate the original goal, append failure context, add any new `deps`, and adjust `scope_paths` when live evidence changed the owner surface.
- Follow-up tasks should target the remaining gap, not redo the full task.
- Corrective developer tasks must instruct the agent to start with systematic diagnosis: run `ci_diagnostics(file_path)` on every file in `scope_paths` and on the files named in the validator's error evidence, identify all errors, then fix them before running verification. Include the exact error snippet from the validator packet so the developer does not re-investigate from scratch.

## Rules

- Must plan new tasks at the current DAG level only. Never decompose into subtrees; assign `team_planner` for work that still needs expansion.
- Must pair each new developer task with a validator task (`cascade_policy: "continue"`).
- Must include exact failing test ids and an error snippet from the validator packet in the new task briefing.
- Must include failure context so the agent does not repeat the same approach.
- Never bundle unrelated fixes into one task.
- Never omit `scope_paths`.
