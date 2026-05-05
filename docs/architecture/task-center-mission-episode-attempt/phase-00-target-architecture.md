# Phase 00 - Target Architecture

## Goal

Define the target harness model before changing implementation details.

The new architecture separates three different ideas that were previously
overloaded onto graph and retry state:

- A `Mission` is the complex delegated goal requested by an executor
  that cannot solve its current task atomically.
- A `Episode` is one request-local execution segment. Segment 2+ exists
  only when the previous segment closed with a non-null `continuation_goal`.
- A `Attempt` is one concrete planner-produced graph for one segment.
  It does not carry retry policy; `EpisodeManager` decides whether a failed
  graph should be followed by another graph in the same segment.

## Executor tool convention

Executor tools use two naming families:

| Prefix      | Meaning                                                                     |
| ----------- | --------------------------------------------------------------------------- |
| `submit_*`  | Terminal outcome for the current executor task.                             |
| `request_*` | Delegated request start that moves the executor task result to another workflow. |

Executor tool surface:

| Tool                            | Meaning                                                                                                                         |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `submit_execution_success`      | The executor completed and verified its assigned task.                                                                          |
| `submit_execution_failure`      | The executor has a scoped failure that cannot be completed directly.                                                            |
| `request_mission_solution` | The assigned task is not atomic; create a planned complex-task workflow whose close report becomes this executor task's result. |

`request_mission_solution` is not a failure terminal. It exits the current
executor agent run by starting a complex-task request. The harness later
attaches a final close report to `requested_by_task_id`; the original executor
agent run ends at the request boundary.

## Target model

```text
USER QUERY
  |
  v
EXECUTOR TASK
  |
  +-- atomic task
  |     `-- submit_execution_success / submit_execution_failure
  |
  `-- non-atomic task
        `-- request_mission_solution(goal)
              |
              v
COMPLEX TASK REQUEST C1
  requested_by_task_id = executor
  goal = requested complex goal
  |
  +-- TASK SEGMENT S1
        sequence_no = 1
        |
        +-- HARNESS GRAPH S1.H1
        |     attempt_sequence_no = 1
        |     status = failed
        |
        `-- HARNESS GRAPH S1.H2
              attempt_sequence_no = 2
              status = passed
              continuation_goal = "<next-segment goal>"

        S1.H2 passes, so S1 closes
        S1.continuation_goal = S1.H2.continuation_goal
        because S1.continuation_goal is not null,
        MissionHandler creates S2

  `-- TASK SEGMENT S2
        sequence_no = 2
        goal = S1.continuation_goal
        |
        `-- HARNESS GRAPH S2.H1
              attempt_sequence_no = 1
              status = passed
              continuation_goal = null

        S2 closes terminal
        C1 closes and reports back to requested_by_task_id
```

The core ownership shape is:

```text
Mission
  requested_by_task_id
  goal
  status
  |
  +-- Episode
        sequence_no
        goal
        attempt_budget
        attempt_ids
        continuation_goal
        |
        `-- Attempt
              attempt_sequence_no
              stage
              status
              continuation_goal
              fail_reason
              |
              +-- planner task
              +-- generator DAG tasks
              |     executors + verifiers
              `-- evaluator task
```

The planner emits a graph contract through `submit_full_plan` or
`submit_partial_plan`. That contract is stored on
`Attempt.task_specification` and `Attempt.evaluation_criteria`.
`submit_partial_plan` also stores `Attempt.continuation_goal`. When a
graph passes, the segment closes. If the passing graph's `continuation_goal` is
non-null, the handler creates the next segment with that goal; otherwise the
mission request closes successfully. When a graph fails, it returns to
`EpisodeManager` for an attempt-budget decision.

Explorer subagents are not TaskCenter nodes; they are non-blocking,
parallel-safe helper runs. Advisor and resolver helper calls are also not
TaskCenter graph nodes. Advisor is read-only. Resolver is blocking and may edit,
but it reports back into the task that called it.

## Three axes of progression

| Axis             | Entity               | What changes                        | Triggered by                                                    | Shape effect                               |
| ---------------- | -------------------- | ----------------------------------- | --------------------------------------------------------------- | ------------------------------------------ |
| Request origin   | `Mission` | new delegated complex goal          | `request_mission_solution(goal)`                           | new request linked to the calling executor |
| Vertical continuation | `Episode` | same request, next segment | passing graph submitted `continuation_goal` through `submit_partial_plan` | segment sequence increases |
| Horizontal retry | `Attempt`       | same segment, fresh graph execution | graph failure followed by a `EpisodeManager` retry decision | graph sequence increases                   |

### Request origin

A `Mission` represents the delegated executor request:

- the executor task that requested help,
- the goal it requested,
- the eventual result attached back to that executor task.

The requesting executor is the stable parent for context management.
`requested_by_task_id` is the authoritative origin and report-delivery link.

### Segment boundary

A `Episode` represents one vertical slice of the mission request. It
carries the segment-local attempt budget and owns the ordered `Attempt`
attempts for that slice.

```text
Mission C
  +-- Episode S1
        +-- Attempt H1
        `-- Attempt H2

  `-- Episode S2
        `-- Attempt H1
```

### Segment close rule

A passing harness graph closes its segment. A failed harness graph either
creates another graph inside the same segment or exhausts the segment:

```text
Episode S has running Attempt H

H passes
  S.continuation_goal = H.continuation_goal
  if S.continuation_goal is null:
    Mission closes succeeded
  else:
    MissionHandler creates Episode N+1
    with goal = S.continuation_goal

H fails
  if attempt budget remains:
    EpisodeManager creates the next Attempt in S
  else:
    S closes failed
    Mission closes failed
```

There is no policy hook for "spend retry on a passed graph": once a graph
passes, it closes the segment. Plan quality is enforced by the evaluator's
pass/fail decision, not by the segment manager.

### Recursive request boundary

Complex-task requests can delegate recursively. Any generator executor inside any
`Attempt` may call `request_mission_solution(goal)`. That creates a
new `Mission`; it does not create a child `Episode` in the outer
request.

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

C2 closes
  |
  v
E7 receives the C2 close report as its final task result inside C1.S1.H1
```

The delegated request has its own segment chain and retry history. The outer
request sees only the close report returned to the executor that requested it.

### Horizontal axis

A `Attempt` is one full `planner -> DAG -> evaluator` pass for one task
segment. When a harness graph fails, `EpisodeManager` decides whether
segment attempt budget should be spent. If it retries, it creates the next
`Attempt` in the same `Episode`.

Retry never creates a new `Mission` or `Episode`.

## Why the split matters

- Complex-task context starts from the executor that requested the solution.
- Segment state reflects a request-local continuation chain and a segment-local
  attempt scope.
- Retry history is derived from the ordered harness graphs inside one task
  segment; it is not encoded as harness-graph identity.
- From the requesting executor task's perspective, one request produces one
  final result.

## Components

| Component                   | Owner / scope                                                  | Responsibility                                                                                                                                                                                                             |
| --------------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Mission`        | `TaskCenter`                                                   | Container for a non-atomic delegated goal. Holds `requested_by_task_id`, goal, status, and final close result.                                                                                                             |
| `Episode`               | `Mission`                                           | One request-local execution segment. Holds sequence, segment goal, attempt budget, ordered `attempt_ids`, and `continuation_goal`.                                                                                   |
| `Attempt`              | `Episode`                                                  | One concrete planner DAG execution: graph sequence within the segment, planner, generator DAG, evaluator, status, `continuation_goal`, and failure reason.                                                                 |
| `MissionHandler` | request boundary / one active handler per `Mission` | Owns the executor request start from `request_mission_solution`, creates and closes the request, creates initial and continuation `Episode`s, spawns their `EpisodeManager`s, and returns the final report.   |
| `EpisodeManager`        | one active manager per `Episode`                           | Owns harness-graph transitions inside one segment: attempt budget, next-graph creation after failed graphs, and segment close. Reports the segment close outcome back to `MissionHandler`.                      |
| `AttemptOrchestrator`  | one per `Attempt`                                         | Runs one planner-produced graph through planner, generator DAG tasks, and evaluator. It reports the graph outcome back to its `EpisodeManager`.                                                                        |
| Tasks                       | per `Attempt`                                             | Planner, executor, verifier, and evaluator agent runs scoped to one harness graph.                                                                                                                                         |

## Runtime Layers

The runtime uses three explicit layers:

| Layer                       | Owns                                                                                                | Does not own                                                  |
| --------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `MissionHandler` | request creation, request close, initial and continuation segment creation, final close report       | retry policy inside a segment or graph execution              |
| `EpisodeManager`        | one segment's attempt budget, next harness graph creation after failed graphs, segment close decision | executor tool boundary, planner/generator/evaluator execution |
| `AttemptOrchestrator`  | one `planner -> generator DAG -> evaluator` execution                                               | retry, continuation, or request close                         |

`MissionHandler` owns:

- `create_mission(requested_by_task_id, goal, context)`,
- `create_initial_segment(mission_id)` -- creates the segment record
  and spawns its `EpisodeManager`,
- `create_continuation_segment(previous_segment, continuation_goal)` -- creates
  the next segment, appends it to `episode_ids`, and spawns its
  `EpisodeManager`,
- `handle_segment_closed(segment_close_report)` -- routes success or failure to
  request close, or routes `success_continue(goal)` to continuation creation,
- `close_mission(mission_id, final_result)`.

`EpisodeManager` owns:

- `create_initial_attempt(episode_id)`,
- `create_next_attempt(episode_id, previous_attempt_id)`,
- `handle_attempt_closed(attempt_id)` -- emits a
  `EpisodeClosureReport` to `MissionHandler` when the segment closes.

`create_next_attempt` follows a failed graph only after
`EpisodeManager` decides to spend segment attempt budget. A passed graph
closes the segment; it never produces another graph in that segment.

The `EpisodeClosureReport` is the only signal `EpisodeManager` sends to
`MissionHandler`:

```text
EpisodeClosureReport {
  episode_id
  final_attempt_id     # the passing graph, or final attempted failed graph
  outcome in {
    terminal_success,        # passing graph with continuation_goal = null
    success_continue(goal),  # passing graph with continuation_goal != null
    attempt_plan_failed {
      failure_summary,
      attempted_plan_history: [
        {
          attempt_summary_id,
          attempt_id,
          attempt_sequence_no,
          task_specification,
          evaluation_criteria,
          fail_reason,
          failure_landscape,
        }
      ]
    },
  }
}
```

`attempt_plan_failed` is a segment outcome, not an attempt-budget label. It means
every planned harness graph attempt for this segment has been tried and all
attempts failed. Its payload is derived from ordered harness graph summaries so
the requester can see what plans were attempted and why each one failed.

`AttemptOrchestrator` decides the outcome of one harness graph.
`EpisodeManager` decides whether that outcome retries inside the segment or
closes the segment. `MissionHandler` decides continuation-segment
creation, request close, and final report delivery.

## Lifecycle Interaction Diagram

The lifecycle has three ownership boundaries:

```text
Executor task E
  |
  | request_mission_solution(goal)
  v
MissionHandler
  |
  | create Mission C
  |   requested_by_task_id = E
  |   status = open
  |
  | create Episode S1
  |   sequence_no = 1
  |   goal = C.goal
  | spawn EpisodeManager(S1)
  v
EpisodeManager(S1)
  |
  | create Attempt S1.H1
  |   attempt_sequence_no = 1
  v
AttemptOrchestrator(S1.H1)
  |
  | run planner -> generator DAG -> evaluator
  |
  +-- graph failed ----------------------------+
  |                                            |
  v                                            |
EpisodeManager(S1)                        |
  |                                            |
  | attempt budget remains                     |
  | create Attempt S1.H2                  |
  v                                            |
AttemptOrchestrator(S1.H2) <-------------+
  |
  | run planner -> generator DAG -> evaluator
  |
  +-- graph passed with continuation_goal != null
  v
EpisodeManager(S1)
  |
  | close Episode S1
  | S1.continuation_goal = S1.H2.continuation_goal
  | emit EpisodeClosureReport { outcome = success_continue(goal) }
  v
MissionHandler
  |
  | create Episode S2
  |   sequence_no = 2
  |   goal = S1.continuation_goal
  | spawn EpisodeManager(S2)
  v
EpisodeManager(S2)
  |
  | create Attempt S2.H1
  |   attempt_sequence_no = 1
  v
AttemptOrchestrator(S2.H1)
  |
  | run planner -> generator DAG -> evaluator
  |
  +-- graph passed with continuation_goal = null
  v
EpisodeManager(S2)
  |
  | close Episode S2
  | emit EpisodeClosureReport { outcome = terminal_success }
  v
MissionHandler
  |
  | close Mission C
  | deliver mission_succeeded report
  v
Executor task E has final complex-task result
```

Failure follows the same boundary:

```text
AttemptOrchestrator(H)
  |
  | graph failed
  v
EpisodeManager(S1)
  |
  +-- attempt budget remains
  |     create next Attempt in the same Episode
  |
  `-- retry exhausted
        close Episode failed
        emit EpisodeClosureReport { outcome = attempt_plan_failed(attempted_plan_history) }
        |
        v
MissionHandler
        |
        close Mission failed
        deliver mission_failed report to requested_by_task_id
```

## Phase exit criteria

- The team agrees that `Mission` is the executor-requested complex
  goal.
- The team agrees that `Episode` is a request-local continuation segment
  with segment-local attempt scope.
- The team agrees that `Attempt` is a planner DAG execution ordered within
  the segment.
- The team agrees that retry is a `EpisodeManager` decision; when it retries,
  it creates another `Attempt` inside the same `Episode`.
- The team agrees that a passed harness graph always closes its segment; a
  non-null `continuation_goal` creates the next segment.
- The team agrees that partial-plan continuation is preserved through
  `submit_partial_plan`, `Attempt.continuation_goal`, and
  `Episode.continuation_goal`.
- The team agrees that `ROOT` is not a creation or spawn reason.
- The context-engine boundary is explicit: planner launch context, per-graph
  evidence, detailed close-report payloads, and segment visibility are specified
  separately.
