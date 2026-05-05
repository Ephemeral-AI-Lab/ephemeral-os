# Phase 04 - Complex Task Spawning

## Goal

Implement complex-task request creation after the durable model, orchestrators,
and tool-gate foundations are in place.

The target model supports partial-plan continuation. A `Mission`
starts with one `Episode`, and `MissionHandler` creates later
segments only when the previous segment closes with a non-null
`continuation_goal`. Segment-manager retry creates new `Attempt`s inside
the current segment.

`MissionHandler` is the only creator of `Mission` and
`Episode` records, and the only spawner of `EpisodeManager` instances.
Each per-segment `EpisodeManager` is the only creator of `Attempt`
records inside its owned segment.

## Phase 01 inheritance

Phase 01 ships request creation, segment-chain construction, and close-report
assembly; Phase 04 wires the actual delivery to `requested_by_task_id`.

**Already in place:**

- `MissionHandler.create_mission(task_center_run_id,
  requested_by_task_id, goal)` creates the request with
  `requested_by_task_id` recorded. `create_initial_segment` /
  `create_continuation_segment` enforce sequence-number contiguity and the
  predecessor SUCCEEDED + non-null `continuation_goal` precondition for
  continuation.
- `MissionCloseReport` DTO carries `mission_id`,
  `requested_by_task_id`, `outcome` (`"success"` | `"failed"`),
  `final_segment_id`, and `final_attempt_id`. It is constructed by
  `MissionHandler._build_close_report` whenever a request closes.
- `MissionHandler.close_mission(...)` invokes a
  `deliver_close_report: Callable[[MissionCloseReport], None] | None`
  callback if one is supplied (the parameter exists; it defaults to
  `None` in Phase 01). Verified end-to-end by
  `test_close_mission_delivers_close_report_when_callback_set`.
- `MissionRecord.final_outcome` is persisted as a JSON dict
  shaped `{"outcome": "success" | "failed", "final_segment_id": ...,
  "final_attempt_id": ...}`.
- `/api/db/task-center-runs/{id}/graph` router currently returns
  `{"attempts": []}` with a `# TODO(phase-04)` comment, so callers
  see the route shape but no data while the new walk is built.
- The integration smoke (`test_integration_smoke.py`) drives every
  segment-closure → request-closure path through stub orchestrators, so
  `MissionStatus.SUCCEEDED` and `MissionStatus.FAILED`
  transitions are already locked.

**Phase 04 wires:**

- A `deliver_close_report` callable that attaches the report to the
  executor task identified by `requested_by_task_id` and unblocks its
  outer agent run.
- The `request_mission_solution` tool handler delegates an accepted call
  to `ComplexTaskHandoffCoordinator`, which calls
  `MissionHandler.create_mission` followed by
  `create_initial_segment`, then exits the executor agent run pending the
  close report (the Phase 03 tool gate guards the same entry point).
- Synchronous close-report delivery to the active parent orchestrator. Phase 04
  deliberately assumes no process restart while a parent task is waiting on a
  delegated request; durable replay/recovery for waiting close reports is future
  runtime recovery work.
- The `/api/db/task-center-runs/{id}/graph` router endpoint, walking
  `missions → episodes → attempts` to surface
  the new schema's harness-graph view to the frontend.

## Creation path

```text
executor task E
  |
  +-- request_mission_solution(goal)
        MissionHandler creates Mission C
          C.requested_by_task_id = E
        MissionHandler creates Episode S1
          S1.goal = C.goal
        MissionHandler spawns EpisodeManager(S1)
        EpisodeManager(S1) creates Attempt H1
```

`request_mission_solution` starts a new complex-task request. It does not
create another segment in an existing request.

## Field mapping

| Creation path | Entity created | Created by | Parent / lineage |
| ------------- | -------------- | ---------- | ---------------- |
| `request_mission_solution` | `Mission` | `MissionHandler` | `requested_by_task_id` is the executor that called the tool |
| initial segment | `Episode` | `MissionHandler` | `mission_id = C`, `sequence_no = 1`, `goal = C.goal` |
| continuation segment | `Episode` | `MissionHandler` | `mission_id = C`, `sequence_no = previous + 1`, `goal = previous_segment.continuation_goal`; the segment id is appended to `episode_ids` |
| initial graph | `Attempt` | `EpisodeManager(S)` | `episode_id = S`, `attempt_sequence_no = 1` |
| subsequent graph after failed graph | `Attempt` | `EpisodeManager(S)` | same `episode_id`, `attempt_sequence_no = previous + 1`; created only after the manager decides to spend attempt budget |

There is no `ROOT` spawn reason. Retry is not `Episode` creation and is not
a `Attempt` creation reason.

## `request_mission_solution` workflow

```text
Executor task E is running inside some harness graph

E calls request_mission_solution(goal)
    |
    v
ComplexTaskHandoffCoordinator starts delegated request handoff
    |
    v
MissionHandler creates Mission C
  requested_by_task_id = E
  goal                 = goal
    |
    v
MissionHandler creates Episode S1 and spawns EpisodeManager(S1)
    |
    v
EpisodeManager(S1) creates Attempt S1.H1
    |
    v
AttemptOrchestrator runs S1.H1 to completion
    |
    v
EpisodeManager(S1) retries inside S1, or closes S1 and emits EpisodeClosureReport
    |
    +-- success_continue(goal)
    |     MissionHandler creates continuation Episode S2
    |     and a fresh EpisodeManager(S2)
    |     EpisodeManager(S2) creates and starts Attempt S2.H1
    |
    v
eventually:
    |
    v
MissionHandler closes C with success or failure
    |
    v
MissionHandler delivers mission_succeeded or
mission_failed report to executor task E
    |
    v
The outer graph consumes E's final task result
```

`request_mission_solution` may happen at any graph depth and during any
generator executor task. The call starts a delegated complex-task request: the
original executor agent run ends at the request boundary and does not submit a
second terminal.

## Recursive complex-task requests

Complex-task requests are recursive. Any generator executor running inside a
`Attempt` can call `request_mission_solution(goal)` before it edits.
That call creates a new `Mission` whose `requested_by_task_id` is the
executor task that called the tool.

```text
Mission C1
  |
  `-- Episode S1
        |
        `-- Attempt S1.H1
              |
              `-- executor task E7
                    |
                    | request_mission_solution(goal)
                    v
              Mission C2
                requested_by_task_id = E7
                |
                +-- Episode S1
                      |
                      `-- Attempt S1.H1
                `-- Episode S2
                      |
                      `-- Attempt S2.H1

C2 closes
  |
  v
MissionHandler returns C2 close report as E7's final task result
```

The delegated request does not become a child `Episode` of the outer request.
The delegated request has its own segment chain and retry history.

## Close reports

A `Attempt` closes exactly once. Its outcome feeds the owning segment.
A `Episode` closes exactly once. Its close report causes
`MissionHandler` to close the request successfully or as failed.

The complex-task close report returned to `requested_by_task_id` has these
harness-owned fields:

| Field | Meaning |
| ----- | ------- |
| `mission_id` | request id |
| `requested_by_task_id` | executor task that requested the complex solution |
| `outcome` | `success` or `failed` |
| `final_segment_id` | segment that produced the final outcome |
| `final_attempt_id` | harness graph that produced the final outcome; `None` only for graph-less entry-segment closes |

For delegated complex-task requests started by `request_mission_solution`,
this field is normally the passing or final failed harness graph. The nullable
case exists for the top-level entry segment, which is closed by
`EntryTaskController` without creating a `Attempt`.

Detailed payload such as per-task summaries, planner scratchpads, and evidence
links belongs to the context engine.

## Close-report routing

| Event | Routing |
| ----- | ------- |
| `Mission` closes | final report is attached to the executor task that called `request_mission_solution` |
| `Episode` closes succeeded | `EpisodeManager` emits `terminal_success`; `MissionHandler` closes the mission request successfully |
| `Episode` closes with continuation | `EpisodeManager` emits `success_continue(goal)`; `MissionHandler` creates the next segment and keeps the request open |
| `Episode` closes failed | `EpisodeManager` emits `attempt_plan_failed(attempted_plan_history)`; `MissionHandler` closes the mission request as failed |

Retry never returns a close report to the requesting executor. Retry is internal
motion inside one task segment.
Continuation also does not return to the requesting executor. It keeps the same
complex request open and creates the next segment.

Current implementation note: graph-mode close-report delivery is synchronous and
process-local. The parent task must still be `waiting_mission`, and its
parent `AttemptOrchestrator` must still be registered in the active
process. If that orchestrator is missing, delivery raises a graph invariant
violation rather than replaying from persisted rows. Reconstructing active
orchestrators and replaying undelivered close reports after process restart is
outside this phase.

## Implementation tasks

1. Implement `request_mission_solution` as a thin tool handler that
   delegates complex-request creation to `ComplexTaskHandoffCoordinator`.
2. Treat `request_mission_solution` as a delegated request start whose
   final result is supplied by the complex-task close report.
3. Create initial `Episode` through `MissionHandler`, spawn
   `EpisodeManager(S1)`, then have the manager create the initial
   `Attempt`.
4. Implement continuation segment creation when a segment closes with
   `success_continue(goal)`, and ensure the fresh `EpisodeManager` creates
   and starts the continuation segment's initial `Attempt`.
5. Route final complex-task close reports back to the requesting executor task
   through the active parent orchestrator.
6. Keep restart-safe close-report replay out of Phase 04; it belongs with
   durable runtime recovery.

## Phase exit criteria

- `request_mission_solution` creates a mission request and its final
  close report becomes the requesting executor task result.
- Each mission request creates its initial task segment, and continuation
  may create later ordered segments.
- Retry stays inside the same segment and does not produce executor close reports
  until the mission request closes.
- Partial-plan continuation creates the next task segment with `goal` set from
  the prior segment's `continuation_goal`.
