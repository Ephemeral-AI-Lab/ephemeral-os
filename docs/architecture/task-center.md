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

When a task fails, TaskCenter marks the original task `replanning` and creates a replanner task. The replanner submits `submit_replan(new_tasks=[...], cancel_ids=[...])`.

After the replan:

- New tasks are inserted at each submitted task's explicit `parent_id`.
- Cancelled sibling tasks are marked `cancelled`.
- Dependents of the original failed task are rewired to the inserted replacement tasks.
- If the replan cannot produce replacement work, the original task fails and cascade handling applies.

## Notes

Notes are scoped by task and path. Task context includes the assigned task, dependency notes, sibling notes, parent context, retry notes, and recent overlapping scope changes.
