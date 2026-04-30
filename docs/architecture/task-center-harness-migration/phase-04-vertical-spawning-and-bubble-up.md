# Phase 04 - Complex Task Spawning and Partial Continuation

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
  +-- S_n closes from accepted graph with plan_shape = partial
        TaskSegmentManager creates TaskSegment S_n+1
        S_n+1.previous_segment_id = S_n
        TaskSegmentManager creates HarnessGraph H1 for S_n+1
```

`request_complex_task_solution` starts a new complex-task request. Partial-plan
continuation extends that same request.

Continuation is based on the accepted closing harness graph for the segment,
not on any earlier harness graph in the same segment.

## Field mapping

| Creation path | Entity created | Parent / lineage |
| ------------- | -------------- | ---------------- |
| `request_complex_task_solution` | `ComplexTaskRequest` | `requested_by_task_id` is the executor that called the tool |
| initial segment | `TaskSegment` | `complex_task_request_id = C`, `previous_segment_id = null`, `sequence_no = 1` |
| partial continuation | `TaskSegment` | `complex_task_request_id = C`, `previous_segment_id = S_n`, `sequence_no = n + 1` |
| initial graph | `HarnessGraph` | `task_segment_id = S`, `retry_no = 1` |
| retry graph | `HarnessGraph` | same `task_segment_id`, `retry_no = previous + 1`; created after failure or after a non-closing partial graph |

There is no `ROOT` spawn reason. Retry is not vertical motion.

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
generator executor task. Gating predicates that inspect partial-continuation
history use the new complex task request's segment chain, so a new request
starts with no prior partial segment.

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
request's partial-continuation history.

## Partial-plan continuation workflow

```
planner in S1.H_k submits submit_partial_plan(
    task_specification,
    evaluation_criteria,
    tasks,
    task_specs,
    continuation_goal
)
    |
    v
S1.H_k runs its partial DAG
    |
    v
evaluator submits success
    |
    v
HarnessGraphOrchestrator marks S1.H_k passed with plan_shape = partial
and reports the graph outcome to TaskSegmentManager
    |
    v
TaskSegmentManager accepts S1.H_k as the closing graph
and closes TaskSegment S1 with plan_shape = partial
    |
    v
TaskSegmentManager creates TaskSegment S2 because S1 closed partial
  complex_task_request_id = C
  previous_segment_id     = S1
  sequence_no             = 2
  goal                    = continuation_goal
    |
    v
TaskSegmentManager creates HarnessGraph S2.H1
    |
    v
planner in S2.H1 sees previous segment already used partial
submit_partial_plan is gated; planner must submit_full_plan
```

The complex task request stays open while continuation segments run. The
request closes only after a full-plan segment succeeds or a segment exhausts
retry budget and fails.

## Segment continuation source of truth

`TaskSegmentManager` creates `TaskSegment N+1` only from the segment's accepted
closing graph:

```text
TaskSegment S1
  |
  +-- HarnessGraph S1.H1
  |     status = passed
  |     plan_shape = partial
  |     not the accepted closing graph
  |
  `-- HarnessGraph S1.H2
        status = passed
        plan_shape = full
        accepted closing graph

S1 closes full.
No TaskSegment S2 is created.
```

This keeps retry history and continuation history separate. Earlier partial
graphs can be part of context, but the last accepted graph is the source of
truth for whether the segment is complete.

If `TaskSegmentManager` decides to spend retry budget after a partial graph to
try for a full plan, that partial graph is not the segment's
`closing_harness_graph_id`. The later accepted graph becomes the source of
truth.

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
| `plan_shape` | `full` or `partial` for the final successful graph when available |

Detailed payload such as per-task summaries, planner scratchpads, and evidence
links belongs to the context engine.

## Close-report routing

| Event | Routing |
| ----- | ------- |
| `ComplexTaskRequest` closes | report returns to the executor task that called `request_complex_task_solution`; that executor resumes from its paused state |
| `TaskSegment` closes with partial success | `TaskSegmentManager` creates the next segment because the previous segment's accepted closing graph was partial; no report is returned to the requesting executor yet |
| `TaskSegment` closes with full success or failure | `TaskSegmentManager` reports a final request outcome; `ComplexTaskRequestHandler` closes the complex task request and returns one final report |

Retry never returns a close report to the requesting executor. Retry is
internal motion inside one task segment.

## Implementation tasks

1. Implement `request_complex_task_solution` creation of `ComplexTaskRequest`
   through `ComplexTaskRequestHandler`.
2. Pause and resume the calling executor around the complex-task close report.
3. Create initial `TaskSegment` and initial `HarnessGraph` through
   `TaskSegmentManager`.
4. Implement partial-continuation `TaskSegment` creation through
   `TaskSegmentManager`.
5. Set `previous_segment_id` on continuation segments.
6. Keep the complex task request open while continuation segments run.
7. Route continuation by creating the next segment rather than returning to the
   requesting executor.
8. Route final complex-task close reports back to the requesting executor.
9. Add close-report persistence or delivery semantics robust enough for
   process restart if the surrounding runtime supports it.

## Phase exit criteria

- `request_complex_task_solution` creates a complex task request and resumes
  the calling executor after the request closes.
- Accepted partial closing graph creates the next task segment in the same
  request.
- Recursive partial plans are gated across previous segments.
- Retry still stays inside the same segment and does not produce executor close
  reports until the complex task request closes.
