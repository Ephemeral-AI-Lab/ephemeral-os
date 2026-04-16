# Team Coordination

EphemeralOS team coordination separates work execution from failure recovery across two core roles: **worker agents** complete assigned tasks, and **replanners** turn failed work into corrective task graph changes.

## Plan And Dispatch

```mermaid
sequenceDiagram
    participant Planner
    participant TaskCenter
    participant DispatchQueue
    participant Worker

    Planner->>TaskCenter: submit_plan(new_tasks=[...])
    TaskCenter->>TaskCenter: validate and insert task DAG
    DispatchQueue->>TaskCenter: pop_ready()
    TaskCenter-->>DispatchQueue: ready task
    DispatchQueue->>Worker: run task with notes and dependencies
    Worker->>TaskCenter: submit_task_summary(type="success")
    TaskCenter->>TaskCenter: mark done and promote dependents
```

## Failure Recovery

```mermaid
sequenceDiagram
    participant Worker
    participant TaskCenter
    participant Replanner

    Worker->>TaskCenter: submit_task_summary(type="fail")
    TaskCenter->>TaskCenter: mark original REPLANNING
    TaskCenter->>Replanner: spawn replanner with failure context
    Replanner->>TaskCenter: submit_replan(new_tasks=[...], cancel_ids=[...])
    TaskCenter->>TaskCenter: apply replan and rewire dependents
```

When a task enters `REPLANNING`, dependent work stays pending. The replanner can add corrective tasks with explicit `parent_id` placement, cancel stale siblings with cascade handling, and provide an `expected_projection` assertion when parent-bounded graph shape matters. If the replanner produces replacement tasks, dependents are rewired from the original failed task to the new task ids. If the replanner fails or produces no replacement work, the original task fails with cascade handling.

## Status Model

Task statuses are:

- `pending`
- `ready`
- `running`
- `expanded`
- `replanning`
- `done`
- `failed`
- `cancelled`

Terminal statuses are `done`, `failed`, and `cancelled`.

## Design Principles

- Worker agents do not change the graph directly; they submit success or failure summaries.
- Replanners are the only agents that mutate the recovery graph through `submit_replan`.
- Ready tasks dispatch as soon as dependencies are satisfied.
- Scope freshness checks protect terminal submissions from stale context.
- Every team task exits through a terminal submission tool: `submit_plan`, `submit_replan`, or `submit_task_summary`.
