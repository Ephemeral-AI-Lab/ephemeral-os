# Phase 01 - Complex Task Request and Harness Graph Model

## Goal

Introduce the durable state model required by the new harness shape before
`MissionHandler`, `EpisodeManager`, and
`AttemptOrchestrator` behavior is migrated.

This phase is mostly schema, persistence, and typed runtime state. It should not
change high-level execution behavior until Phase 02 starts using the new model.

Each `Mission` tracks its owned `Episode` records with an
ordered `episode_ids` list. Phase 01 only needs to create the initial
segment, but the request shape must allow more than one segment. Retry creates
additional `Attempt` records inside the current segment.

## Durable entities

### `Mission`

A `Mission` is a complex delegated goal requested by an executor that
decided its assigned task is not atomic.

Required fields:

| Field | Meaning |
| ----- | ------- |
| `id` | Mission request id. |
| `task_center_run_id` | Owning TaskCenter run. Do not use the generic `run_id` name here; agent execution records have their own run ids. |
| `requested_by_task_id` | Executor task that called `request_mission_solution`. |
| `goal` | Goal supplied by the executor. |
| `status` | `open`, `succeeded`, `failed`, or `cancelled`. |
| `episode_ids` | Ordered list of `Episode` ids owned by this request. Starts with the initial segment and may contain multiple segments. |
| `created_at` / `updated_at` / `closed_at` | Lifecycle timestamps. |

`requested_by_task_id` is the authoritative parent link for context and final
result routing.

### `Episode`

A `Episode` is one request-local execution scope for a mission request.
The initial segment starts from the requested goal; later segments use the goal
captured from the previous segment's `continuation_goal`. Each segment owns
attempt budget for harness graph attempts.

Required fields:

| Field | Meaning |
| ----- | ------- |
| `id` | Segment id. |
| `mission_id` | Owning mission request. |
| `sequence_no` | 1-based segment order within the request. |
| `creation_reason` | `initial` or `partial_continuation`. |
| `goal` | Segment goal. Segment 1 equals the request goal. Segment 2+ equals the previous segment's `continuation_goal`. |
| `attempt_budget` | Maximum harness graph attempts for this segment. |
| `status` | `open`, `succeeded`, `failed`, or `cancelled`. |
| `attempt_ids` | Ordered list of `Attempt` ids owned by this segment. Attempts can be inferred from this list. |
| `continuation_goal` | Set when the segment closes from the passing harness graph that closed it. Null while the segment is open. Null on terminal close or failure; non-null when the passing graph submitted a partial plan. |

Segment ordering is recorded by `Mission.episode_ids` and
`Episode.sequence_no`; there is no `previous_segment_id` lineage.
Continuation lineage is derived from adjacent segments in that ordered list.

### `Attempt`

A `Attempt` is one full planner-produced graph execution for one segment.
It runs `planner -> generator DAG -> evaluator`. Retry policy is not stored on
the graph; `EpisodeManager` decides whether a failed graph should be
followed by another graph in the same segment.

```text
Attempt {
    segment_id:          owning Episode
    attempt_sequence_no:   1-based graph order inside the segment
    stage:               planning | generating | evaluating | closed
    planner_task_id:     uuid
    task_specification:  string from submit_full_plan or submit_partial_plan
    evaluation_criteria: [criterion, ...]
    generator_task_ids:  [executor_1, verifier, ...]
    evaluator_task_id:   null | uuid
    status:              running | passed | failed
    continuation_goal:   null
                       | string (set from submit_partial_plan)
    fail_reason:         null
                       | planner_failed
                       | generator_failed
                       | evaluator_failed
}
```

Per-harness-graph evidence such as task summaries, planner scratchpads, and
artifact references belongs to the context engine. The harness model stores only
the structural state needed for lifecycle decisions.

`task_specification` and `evaluation_criteria` are the segment contract emitted
by the planner. `AttemptOrchestrator` passes them to the evaluator as
evaluation instructions. The harness graph that passes closes its segment, and
its contract is the segment's accepted record.

`continuation_goal` is set per harness graph when that graph's planner submits
`submit_partial_plan`. A later harness graph in the same segment does not
inherit `continuation_goal` from a prior failed graph; the new planner decides
independently whether to submit a full plan or a partial plan. The segment's
`continuation_goal` is copied only from the passing harness graph that closes
the segment.

Generator ordering and dependency constraints live on task records rather than
on `Attempt`.

`evaluator_task_id` is unset while the graph is in `planning` or `generating`.
`AttemptOrchestrator` creates the evaluator only after every generator task
in the current graph has completed successfully.

There is no `ROOT` spawn or creation reason.

## Creation reasons and lineage

| Entity | Creation reason | Trigger | Parent / lineage |
| ------ | --------------- | ------- | ---------------- |
| `Mission` | implicit complex-task request | Executor calls `request_mission_solution(goal)` | `requested_by_task_id` points to the executor. |
| `Episode` | `initial` | Mission request starts | `mission_id` points to the request; the segment id is appended to `episode_ids`. |
| `Episode` | `partial_continuation` | Prior segment closed with non-null `continuation_goal` | `mission_id` points to the request; the segment id is appended to `episode_ids`, and `goal` is set from the prior segment's `continuation_goal`. |
| `Attempt` | none | Segment manager starts a graph execution | `segment_id` points to the owned segment; `attempt_sequence_no = 1` for the first graph, or previous + 1 for later graphs. |

Retry is never a `Mission` or `Episode` creation reason. It is a
`EpisodeManager` decision after a failed graph within the current segment.
A passing harness graph closes its segment; it never produces another graph.
When the passing graph has a non-null `continuation_goal`,
`MissionHandler` creates the next segment with that goal.

## Context walks

Three context walks coexist:

- Request origin: `Mission.requested_by_task_id`.
- Request segment order and continuation: `Mission.episode_ids`
  plus `Episode.sequence_no`; segment N+1's `goal` equals segment N's
  `continuation_goal`.
- Horizontal graph history: `Attempt.segment_id` plus lower
  `attempt_sequence_no` values. Retry context is derived from prior failed graphs
  by `EpisodeManager` and the context engine.

The context engine can compose these into:

```text
Mission
  goal = goal from requesting executor
  episode_ids = [S1, ...]
  |
  +-- Episode 1
  |     |
  |     +-- Attempt 1
  |     |     initial try, failed
  |     |
  |     `-- Attempt 2
  |           second graph after failed graph, passed with continuation_goal != null
  |           segment 1 closes with continuation_goal copied from Attempt 2
  |           MissionHandler creates Episode 2
  |
  +-- Episode 2
  |     goal = Episode 1 continuation_goal
  |     |
  |     `-- Attempt 1
  |           passed with continuation_goal = null
  |           segment 2 closes terminal
  |
  `-- Mission closes and reports back to requested_by_task_id
```

## Segment retry policy

`Episode.attempt_budget` is set at segment creation. It may come from a
runtime default or request-level configuration, but it is applied segment-locally.

`Episode.attempt_ids` is the ordered source of truth for harness graph
attempts within a segment. Expose a public `get_attempt_count(episode)`
helper that returns the count derived from `attempt_ids` rather than
storing a separate counter.

Continuation does not inherit prior segments' attempt count. Each segment has
its own `attempt_budget`.

## Lifecycle Services

Add three lifecycle services. Runtime tool handlers and
`AttemptOrchestrator`s should not manually assemble
`Mission`, `Episode`, or `Attempt` records.

`MissionHandler` owns the request boundary and attaches request
segments. It is the only creator of `Mission` and `Episode`
records:

| Method | Responsibility |
| ------ | -------------- |
| `create_mission(...)` | Create the request from `request_mission_solution`, set `requested_by_task_id`, store the goal, and initialize request status. |
| `create_initial_segment(...)` | Create segment 1 with `goal = request.goal`, set attempt budget, append it to `request.episode_ids`, and spawn a `EpisodeManager` bound to that segment. |
| `create_continuation_segment(...)` | Create segment N+1 only after segment N closes with non-null `continuation_goal`; set `sequence_no = N+1`, `goal = previous_segment.continuation_goal`, append it to `request.episode_ids`, and spawn a `EpisodeManager` bound to that segment. |
| `handle_segment_closed(...)` | Receive the `EpisodeClosureReport` from the per-segment `EpisodeManager`; route `terminal_success` and `attempt_plan_failed` to request close, and route `success_continue(goal)` to `create_continuation_segment`. |
| `close_mission(...)` | Store the final result and attach the complex-task close report to `requested_by_task_id`. |

`EpisodeManager` is per-`Episode` and owns harness-graph transitions
inside that one segment. It is the only creator of `Attempt` records:

| Method | Responsibility |
| ------ | -------------- |
| `create_initial_attempt(...)` | Create graph sequence 1 for the owned segment and append it to `attempt_ids`. |
| `create_next_attempt(...)` | Create graph sequence N+1 in the same segment after a failed harness graph and segment attempt-budget check. |
| `handle_attempt_closed(...)` | React to a graph outcome by either retrying inside the segment or copying a passing graph's `continuation_goal` onto the segment, closing the segment, and emitting a `EpisodeClosureReport` to `MissionHandler`. |
| `get_attempt_count(episode)` | Public helper that returns the number of harness graph attempts from `attempt_ids`. |

`EpisodeClosureReport` is the only signal from `EpisodeManager` to
`MissionHandler`:

```text
EpisodeClosureReport {
  episode_id
  final_attempt_id     # passing graph, or final attempted failed graph
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

`attempt_plan_failed` contains the ordered plan attempts for the segment. Each
entry is derived from a closed harness graph summary and records both the plan
that was tried and the failure reason or failure landscape for that graph.

`EpisodeManager` must enforce these invariants for graph-mode task
segments. The graph-less entry segment introduced later in Phase 06 is a narrow
carve-out owned by `EntryTaskController`; it does not use `EpisodeManager`
or create `Attempt` rows.

- Subsequent harness graphs stay in the same segment.
- Graph sequence numbers are contiguous within a segment.
- A passing harness graph always closes the owned segment; it never produces a
  subsequent graph in the same segment.
- A segment's `continuation_goal` is copied only from the passing harness graph
  that closes it.
- A failed harness graph returns to `EpisodeManager`; the manager retries
  while attempt budget remains, and closes the segment failed once budget is exhausted.
- A graph-mode segment is initialized with exactly one initial harness graph and
  closes exactly once.
- The manager never creates `Mission` or `Episode` records.

`MissionHandler` must enforce these invariants:

- Every mission request has one or more ordered `Episode` ids in
  `episode_ids`.
- `episode_ids` contains each segment owned by the request exactly once.
- Segment N+1 can only be created from a closed segment whose
  `continuation_goal` is non-null.
- Segment N+1's `goal` equals segment N's `continuation_goal`.
- Exactly one `EpisodeManager` instance is active per open segment.
- A request, segment, or graph is initialized in exactly one valid opening
  state.

## Implementation tasks

1. Add or adapt typed models for `Mission`, `Episode`, and
   `Attempt`.
2. Add persistence fields for request origin, ordered `episode_ids`, attempt
   budget, ordered `attempt_ids`, graph sequence, harness graph stage,
   `continuation_goal`, and failure reason.
3. Scope planner, generator, verifier, and evaluator task ids to a
   `Attempt`.
4. Add `MissionHandler` as the only creator and closer of
   `Mission` records, the only creator of `Episode` records for a
   request, and the spawner of one `EpisodeManager` per created segment.
5. Add `EpisodeManager` as the only creator of `Attempt` records inside
   its owned segment, and the sole emitter of `EpisodeClosureReport`.
6. Add repository/store helpers used by the lifecycle services for:
   - inserting a mission request,
   - inserting the initial task segment,
   - inserting a continuation task segment,
   - inserting the next harness graph after a segment-manager retry decision,
   - loading ordered segments for a request,
   - loading the current segment for a request,
   - loading the current harness graph for a segment from the last
     `attempt_ids` entry,
   - walking `requested_by_task_id`,
   - listing harness graphs by segment and graph sequence order.
7. Backfill or compatibility-map existing graph-as-attempt state as needed.

## Phase exit criteria

- The runtime can create and load a `Mission`.
- The runtime can create segment 1 with harness graph sequence 1.
- Tests cover `request_mission_solution` creating a request linked to
  `requested_by_task_id`.
- Tests prove each request records created segments in `episode_ids`.
- Tests prove `episode_ids` can hold multiple `Episode` ids for one
  request.
- Tests cover continuation creating `Episode` N+1 with `goal` set from the
  previous segment's `continuation_goal`.
- Tests prove `EpisodeManager` retry creates another `Attempt` in the
  same segment, not a new segment or request.
