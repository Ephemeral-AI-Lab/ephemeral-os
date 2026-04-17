# Task Center

TaskCenter owns the team task graph, notes, status transitions, budget counters, and replan application. It is the coordination layer between planners, workers, replanners, and the dispatch queue.

## Responsibilities

- Insert validated plans into the task DAG.
- Track task status and dependency readiness.
- Build task context from dependency notes, sibling notes, parent context, retry state, and recent scope changes.
- Mark work complete or failed.
- Spawn replanner tasks when a worker submits failure.
- Apply replanner output by inserting new tasks, cancelling stale tasks, and rewiring dependents.

## Statuses

Task statuses are `pending`, `ready`, `running`, `expanded`, `replanning`, `done`, `failed`, and `cancelled`.

`done`, `failed`, and `cancelled` are terminal.

## Replanning

When a task fails, TaskCenter marks the original task `replanning`, creates a replanner task, and rewires every non-terminal dependent from the failed task to the replanner. The replanner submits `submit_replan(new_tasks=[...], cancel_ids=[...])`.

After the replan:

- New tasks are inserted at each submitted task's explicit `parent_id`; this may be the replanner itself, the replanner's parent, or a surviving task inside that parent projection.
- Cancelled not-completed tasks are marked `cancelled`, including cascaded descendants and dependents.
- The replanner is marked `done` immediately when it has no direct child tasks, or `expanded` when it created direct child tasks.
- Expanded replanners are marked `done` only after all direct children finish successfully.
- The original failed task is marked `failed` without cascading after the replanner succeeds, because dependents have already been rewired to the replanner.

## Notes

Notes are scoped by task and path. Task context includes the assigned task, dependency notes, sibling notes, parent context, retry notes, and recent overlapping scope changes.
