# Phase 04 - Complex Task Spawning and Continuation

## Goal

Implement complex-task request creation and vertical task-segment continuation
after the durable model, orchestrators, and tool-gate foundations are in place.

Vertical motion creates new `TaskSegment`s inside one `ComplexTaskRequest`.
Horizontal retry creates new `HarnessGraph`s inside one segment.

Request creation in this phase goes through `ComplexTaskRequestHandler`.
Segment and harness graph creation go through `TaskSegmentManager`.

## Creation paths

```
executor task E
  |
  +-- request_complex_task_solution(goal)
        ComplexTaskRequestHandler creates ComplexTaskRequest C
        C.requested_by_task_id = E
        TaskSegmentManager creates TaskSegment S1
        TaskSegmentManager creates HarnessGraph H1

TaskSegment S_n
  |
  +-- S_n.continuation_goal is not null
        TaskSegmentManager creates TaskSegment S_n+1
        S_n+1.previous_segment_id = S_n
        S_n+1.goal = S_n.continuation_goal
        TaskSegmentManager creates HarnessGraph H1 for S_n+1
```

`request_complex_task_solution` starts a new complex-task request. Continuation
extends that same request.

Continuation is based on the segment's `continuation_goal`, which is set only
from the passing harness graph that closes the segment.

## Field mapping

| Creation path | Entity created | Parent / lineage |
| ------------- | -------------- | ---------------- |
| `request_complex_task_solution` | `ComplexTaskRequest` | `requested_by_task_id` is the executor that called the tool |
| initial segment | `TaskSegment` | `complex_task_request_id = C`, `previous_segment_id = null`, `sequence_no = 1`, `goal = C.goal` |
| continuation | `TaskSegment` | `complex_task_request_id = C`, `previous_segment_id = S_n`, `sequence_no = n + 1`, `goal = S_n.continuation_goal` |
| initial graph | `HarnessGraph` | `task_segment_id = S`, `retry_no = 1` |
| retry graph | `HarnessGraph` | same `task_segment_id`, `retry_no = previous + 1`; created after a failed graph |

There is no `ROOT` spawn reason. Retry is not vertical motion. A retry graph's
`continuation_goal` is decided independently by its own planner; it is not
inherited from the prior failed graph.

## `request_complex_task_solution` workflow

```
Executor task E is running inside some harness graph

E calls request_complex_task_solution(goal)
    |
    v
ComplexTaskRequestHandler creates ComplexTaskRequest C
  requested_by_task_id = E
  goal                 = goal
    |
    v
TaskSegmentManager creates TaskSegment S1
    |
    v
TaskSegmentManager creates HarnessGraph S1.H1
    |
    v
HarnessGraphOrchestrator runs S1.H1 to completion
    |
    v
TaskSegmentManager handles retry, continuation, or final segment outcome
    |
    v
ComplexTaskRequestHandler delivers complex_task_succeeded or
complex_task_failed report
back to executor E
    |
    v
executor E resumes and eventually submits execution success or failure
```

`request_complex_task_solution` may happen at any graph depth and during any
generator executor task. Gating predicates that inspect continuation history
use the new complex task request's segment chain, so a new request starts with
no prior continuation history.

## Recursive complex-task requests

Complex-task requests are recursive. Any generator executor running inside a
`HarnessGraph` can call `request_complex_task_solution(goal)` before it edits.
That call creates a new `ComplexTaskRequest` whose `requested_by_task_id` is
the executor task that called the tool.

```text
ComplexTaskRequest C1
  |
  `-- TaskSegment S1
        |
        `-- HarnessGraph S1.H1
              |
              `-- executor task E7
                    |
                    | request_complex_task_solution(goal)
                    v
              ComplexTaskRequest C2
                requested_by_task_id = E7
                |
                `-- TaskSegment S1
                      |
                      `-- HarnessGraph S1.H1

C2 closes
  |
  v
ComplexTaskRequestHandler returns C2 close report to E7
  |
  v
E7 resumes inside C1.S1.H1
```

Only the requesting executor is paused. The nested request does not become a
child `TaskSegment` of the outer request, and it does not use the outer
request's continuation history.

## Partial-plan continuation workflow

```
planner in S1.H_k submits submit_partial_plan(
    task_specification,
    evaluation_criteria,
    tasks,
    task_specs,
    continuation_goal = G
)
    |
    v
S1.H_k.continuation_goal = G          (set on this graph only)
    |
    v
S1.H_k runs its partial DAG
    |
    v
evaluator submits success
    |
    v
HarnessGraphOrchestrator marks S1.H_k passed
and reports the graph outcome to TaskSegmentManager
    |
    v
TaskSegmentManager closes TaskSegment S1
S1.continuation_goal = S1.H_k.continuation_goal = G
    |
    v
TaskSegmentManager creates TaskSegment S2 because S1.continuation_goal != null
  complex_task_request_id = C
  previous_segment_id     = S1
  sequence_no             = 2
  goal                    = S1.continuation_goal
    |
    v
TaskSegmentManager creates HarnessGraph S2.H1
    |
    v
planner in S2.H1 sees previous segment already used a partial plan
submit_partial_plan is gated; planner must submit_full_plan
```

The complex task request stays open while continuation segments run. The
request closes only after a terminal segment succeeds (passing graph with
`continuation_goal = null`) or a segment exhausts retry budget and fails.

## Segment continuation source of truth

`TaskSegmentManager` creates `TaskSegment N+1` only when the previous segment
closed with non-null `continuation_goal`. The segment's `continuation_goal` is
inherited from the harness graph that closed the segment — which is the
passing (last successful) harness graph in that segment, since failed graphs
trigger retry rather than closing.

Each harness graph's `continuation_goal` is set independently by its own
planner submission. A retry graph does not inherit `continuation_goal` from
prior failed graphs in the same segment. The segment learns its
`continuation_goal` only when one of its harness graphs passes.

## Close reports

A `HarnessGraph` closes exactly once. Its outcome feeds the owning segment.
A `TaskSegment` closes exactly once. Its outcome either creates a continuation
segment, closes the request successfully, or closes the request as failed.

The complex-task close report returned to `requested_by_task_id` has these
harness-owned fields:

| Field | Meaning |
| ----- | ------- |
| `complex_task_request_id` | request id |
| `requested_by_task_id` | executor task that requested the complex solution |
| `outcome` | `success` or `failed` |
| `final_segment_id` | segment that produced the final outcome |
| `final_harness_graph_id` | harness graph that produced the final outcome |

Detailed payload such as per-task summaries, planner scratchpads, and evidence
links belongs to the context engine.

## Close-report routing

| Event | Routing |
| ----- | ------- |
| `ComplexTaskRequest` closes | report returns to the executor task that called `request_complex_task_solution`; that executor resumes from its paused state |
| `TaskSegment` closes with non-null `continuation_goal` | `TaskSegmentManager` creates the next segment; no report is returned to the requesting executor yet |
| `TaskSegment` closes with null `continuation_goal` (terminal) or as failed | `TaskSegmentManager` reports a final request outcome; `ComplexTaskRequestHandler` closes the complex task request and returns one final report |

Retry never returns a close report to the requesting executor. Retry is
internal motion inside one task segment.

## Implementation tasks

1. Implement `request_complex_task_solution` creation of `ComplexTaskRequest`
   through `ComplexTaskRequestHandler`.
2. Pause and resume the calling executor around the complex-task close report.
3. Create initial `TaskSegment` and initial `HarnessGraph` through
   `TaskSegmentManager`.
4. Implement continuation `TaskSegment` creation through `TaskSegmentManager`
   when the previous segment's `continuation_goal` is non-null.
5. Set `previous_segment_id` and `goal` on continuation segments.
6. Keep the complex task request open while continuation segments run.
7. Route continuation by creating the next segment rather than returning to the
   requesting executor.
8. Route final complex-task close reports back to the requesting executor.
9. Add close-report persistence or delivery semantics robust enough for
   process restart if the surrounding runtime supports it.

## Phase exit criteria

- `request_complex_task_solution` creates a complex task request and resumes
  the calling executor after the request closes.
- A passing harness graph with non-null `continuation_goal` closes its segment
  and creates the next task segment in the same request.
- A retry harness graph's `continuation_goal` is set only by its own planner
  and not inherited from prior failed graphs.
- Recursive partial plans are gated across previous segments.
- Retry stays inside the same segment and does not produce executor close
  reports until the complex task request closes.
