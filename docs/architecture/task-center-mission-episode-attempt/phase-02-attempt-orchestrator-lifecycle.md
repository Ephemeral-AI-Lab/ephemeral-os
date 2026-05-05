# Phase 02 - Harness Graph Orchestrator Lifecycle

## Goal

Move single-harness-graph execution decisions into `AttemptOrchestrator`.

The lifecycle split is:

- `MissionHandler` owns the request boundary and the segment chain:
  `request_mission_solution`, request creation, initial-segment creation,
  continuation-segment creation, request close, and final close-report delivery
  to `requested_by_task_id`.
- `EpisodeManager` is per-`Episode` and owns harness-graph transitions
  inside one segment: attempt-budget decisions, next-harness-graph creation after
  failed graphs, and segment close. It emits a `EpisodeClosureReport` to
  `MissionHandler` when its segment closes.
- `AttemptOrchestrator` owns one `Attempt` execution:
  `planner -> generator tasks -> evaluator`.

`AttemptOrchestrator` is in-process and ephemeral. Durable state lives on
`Mission`, `Episode`, `Attempt`, tasks, and task outputs.

## Phase 01 inheritance

Phase 01 ships the orchestrator's contract surface and every persistence
seam this phase needs; Phase 02 only fills behaviour.

**Already in place:**

- `AttemptOrchestrator` class skeleton at
  `backend/src/task_center/attempt/orchestrator.py`
  with constructor signature `(attempt, graph_store, on_graph_closed)`.
  Phase 02 should replace the old terminal-handler placeholder surface with
  `start()`, public `apply_*` terminal-submission entries, and private
  mutation/dispatch helper groups.
- `AttemptStore` mutators that the orchestrator drives stage-by-stage:
  `set_planner_task_id`, `set_plan_contract(task_specification,
  evaluation_criteria, continuation_goal)`, `set_generator_task_ids`,
  `set_evaluator_task_id`, `set_stage`, and `close(status, fail_reason,
  closed_at)`.
- `EpisodeManager.__init__` accepts an optional
  `orchestrator_factory` parameter (`None` in Phase 01). Phase 02 should tighten
  the factory shape to
  `Callable[[Attempt, Callable[[str], None]], AttemptOrchestrator]`
  so each manager can pass its own `handle_attempt_closed` callback when
  it starts a graph.
- `EpisodeManager.handle_attempt_closed` is the callback the
  orchestrator's `on_graph_closed` already targets. The closure routing
  (`PASSED → terminal_success | success_continue`,
  `FAILED → retry or attempt_plan_failed`) is already verified by
  `backend/tests/task_center/lifecycle/test_integration_smoke.py`, which
  exercises the full pipeline through a synchronous stub orchestrator.
- Graph-level invariants
  (`assert_graph_running`, `assert_graph_sequence_contiguous`,
  `assert_fail_reason_present_on_failure`) live in
  `task_center.attempt.validation` and
  raise `GraphInvariantViolation` on breach.

**Phase 02 wires:**

- A `AttemptOrchestrator` with `start()` and one public `apply_*`
  entry per close path (`apply_plan_submission`, `apply_planner_failure`,
  `apply_generator_submission`, `apply_evaluator_submission`).
  Phase 04 ships `apply_mission_close_report` for delegated-request resume.
- Private mutation helpers for graph-owned task writes and private dispatch
  helpers for launchability, generator-failure quiescence, evaluator spawn, and
  graph close.
- An orchestrator factory passed to every `EpisodeManager` spawned by
  `MissionHandler._spawn_segment_manager`.
- Calls to `graph_store.set_*` mutators as the orchestrator advances the plan
  contract and stages `planning → generating → evaluating`. The persisted close
  via private `_close_graph(...)` is followed by the `on_graph_closed`
  callback.

## Responsibility Boundary

`AttemptOrchestrator` never creates `Mission`, `Episode`, or
sibling `Attempt` rows. It receives a current `Attempt`, runs it to a
passed or failed outcome, and reports that outcome to its owning
`EpisodeManager`.

Terminal tools also remain outside lifecycle policy. They parse public tool
input, enforce role gates, return user-facing tool errors, and end the agent
run. After validation succeeds, terminal tools call the matching
`AttemptOrchestrator.apply_*` method with a typed submission DTO. The
orchestrator owns the resulting state transition, then runs its private
dispatch helpers to launch follow-up work or close the graph.

`EpisodeManager` then decides, inside its owned segment, whether to:

- create another `Attempt` after a failed graph when attempt budget remains,
- close the current segment and emit a `EpisodeClosureReport`.

`EpisodeManager` never creates a continuation `Episode`. When a passing
graph closes the segment with a non-null `continuation_goal`, the manager emits
`success_continue(goal)` and `MissionHandler` creates the next
segment and its fresh `EpisodeManager`.

`MissionHandler` closes the `Mission` and delivers the
final report when it receives a `EpisodeClosureReport` with
`terminal_success` or `attempt_plan_failed`.

## Harness Graph Orchestrator Responsibilities

For one `Attempt`, `AttemptOrchestrator`:

1. Exposes public `apply_*` methods that accepted terminal tool handlers call
   with typed submission DTOs.
2. Uses private mutation helpers to write graph-owned task and graph state.
3. Uses private dispatch helpers to decide when tasks can launch and when graph
   state should be escalated to the owning segment.

Graph close is a state mutation followed by the existing
`on_graph_closed(graph_id)` callback to `EpisodeManager`.

### Public submission entries

These entries are methods on `AttemptOrchestrator`. They are not public
tool handlers and do not parse public tool input. Terminal tool handlers are
the callers; they validate input and role gates first, then pass typed DTOs.

| Entry | Called by | Receives | Mutates |
| ----- | --------- | -------- | ------- |
| `apply_plan_submission(...)` | `submit_full_plan` or `submit_partial_plan` handler | `PlannerSubmission` | Persists graph contract, stores `continuation_goal` for partial plans, creates all generator task rows, sets graph stage to `generating`, then dispatches ready work. |
| `apply_planner_failure(...)` | runtime (no tool handler — see note below) | `PlannerFailureSubmission` | Marks the planner task `failed` with the runtime-supplied summary, then closes the graph failed via `_close_graph(planner_failed)`. |
| `apply_generator_submission(...)` | `submit_execution_*` or `submit_verification_*` handler | `GeneratorSubmission` | Appends the role-tagged summary and marks the generator task `done` or `failed`; on failure, blocks pending descendants. |
| `apply_evaluator_submission(...)` | `submit_evaluation_success` or `submit_evaluation_failure` handler | `EvaluatorSubmission` | Appends evaluator summary and marks the evaluator task `done` or `failed`; dispatch then closes the graph passed or failed. |

`apply_plan_submission(...)` is the unified planner success entry. It receives
a normalized `PlannerSubmission` with `kind = full | partial`; it does not expose
separate orchestrator methods for `submit_full_plan` and
`submit_partial_plan`.

`apply_planner_failure(...)` is called by the runtime, not a tool handler. The
planner has no agent-facing failure terminal in Phase 02; instead, when a planner
agent run ends without a valid `submit_*_plan` call, the runtime synthesizes a
`PlannerFailureSubmission` and routes it through this entry. This keeps all four
close paths on a uniform `apply_*` surface even though the planner has only one
agent-facing success path.

Executor and verifier submissions both produce generator task outcomes and
share `apply_generator_submission`. Their public tool contracts (separate
schemas, role gates, and user-facing errors) live in the Phase 03 tool
handlers, which validate, normalize into a `GeneratorSubmission`, and stamp
the role into the summary/payload before dispatching to the orchestrator.

`AttemptOrchestrator` must not handle generator `submit_request_plan` or
any legacy request-plan tool. Request-style generator delegation is not a
success or failure task outcome. The supported request-start path is
`request_mission_solution`, which belongs to the complex-task request
boundary described in Phase 04. If a legacy `submit_request_plan` surface still
exists during migration, it should be rejected or aliased before reaching
`AttemptOrchestrator`.

### Private dispatch helpers

Dispatch is a private helper group inside one `AttemptOrchestrator`. It
reads persisted task state after mutations and decides the next launch or graph
outcome.

It owns:

- launching dependency-free generator tasks after a plan is accepted,
- launching generator tasks whose dependencies are all `DONE`,
- launching the evaluator only after every generator task is `DONE`,
- blocking pending descendants after a generator failure,
- holding graph failure escalation until generator quiescence,
- closing the graph and notifying `EpisodeManager` when the graph outcome
  is known.

Generator failures include executor and verifier failures. When an executor
task fails, the orchestrator marks descendants that can no longer run as `BLOCKED`,
continues to let already-running independent generator tasks finish, and does
not report graph failure to the segment until all non-blocked work has reached a
terminal state. This preserves useful sibling evidence for the next retry plan.

Evaluator failure escalates immediately because the evaluator is launched only
after generator quiescence has already been reached.

## Harness graph stages

| Stage | Running work | Exit condition |
| ----- | ------------ | -------------- |
| `planning` | planner task | planner submits a valid plan, or planner run ends without valid submission |
| `generating` | generator tasks | all generators are terminal |
| `evaluating` | evaluator task | evaluator submits success or failure |
| `closed` | none | harness graph is passed or failed |

Leaving `generating` does not always create an evaluator. If every generator is
`DONE`, `AttemptOrchestrator` creates the evaluator and moves to
`evaluating`. If any generator is `FAILED` or `BLOCKED`, the graph closes as
failed after generator quiescence.

`request_mission_solution` starts a delegated request for a generator task.
The requesting generator agent run exits after the request tool call; the outer
graph receives that task's final result when the delegated mission request
closes.

## Failure escape valves

```text
Failure escape valves:
  - Tool-call-level error from any agent
      prehook or handler returns ToolResult(is_error=True)
      -> agent retries inside its own run
      -> no harness-graph-level escalation

  - Generator executor/verifier submit_*_failure terminal call
      tool handler validates and calls
        AttemptOrchestrator.apply_generator_submission(...)
      -> mutation marks generator task failed and blocks pending descendants
      -> _dispatch_ready_work() waits for generator quiescence
      -> _close_graph(FAILED, generator_failed)

  - Evaluator submit_evaluation_failure terminal call
      tool handler validates and calls
        AttemptOrchestrator.apply_evaluator_submission(...)
      -> mutation marks evaluator task failed
      -> _dispatch_ready_work() observes failed evaluator
      -> _close_graph(FAILED, evaluator_failed)

  - Planner agent ends without a successful submit_*_plan
      -> runtime synthesizes a PlannerFailureSubmission and calls
         AttemptOrchestrator.apply_planner_failure(submission)
      -> mutation marks the planner task failed with the runtime-supplied summary
      -> _close_graph(FAILED, planner_failed)
```

The planner has no agent-facing failure terminal — it cannot self-declare
failure via a tool call. Its only soft-fail channel is inline tool-call
rejection. When a planner run ends without a valid plan submission, the
runtime escalates to `AttemptOrchestrator.apply_planner_failure(...)`
with a synthesized `PlannerFailureSubmission`; that entry delegates to
`_close_graph(FAILED, planner_failed)`.

## Harness Graph Failures

| Failure mode | Detected by | Wait point |
| ------------ | ----------- | ---------- |
| `planner_failed` | runtime ends planner without valid plan submission | immediate |
| `generator_failed` | generator submitted failure | wait until every generator is `DONE`, `FAILED`, or `BLOCKED`. `WAITING_COMPLEX_TASK` is non-terminal and keeps the graph in `generating` until the delegated request resumes the outer task. |
| `evaluator_failed` | evaluator submitted `submit_evaluation_failure` | immediate |

### Generator-failure quiescence

- When a generator fails, its dependents transition to `BLOCKED`.
- Independent sibling generators keep running.
- `AttemptOrchestrator` does not retry mid-flight.
- After all generators are in `DONE`, `FAILED`, or `BLOCKED`,
  `AttemptOrchestrator` makes one harness-graph-level outcome decision.
- If `EpisodeManager` spends attempt budget, it creates the next harness
  graph; that graph's planner receives the whole failure landscape through the
  context engine.

### Evaluator failure

The evaluator is spawned only after every generator is `DONE`, so quiescence is
already satisfied. Evaluator failure triggers harness graph failure immediately.

## Harness Graph Outcome

```text
close_attempt(H, outcome):
    H.status            = passed | failed
    H.stage             = closed
    H.continuation_goal = null
                        | string (set from submit_partial_plan)
    H.fail_reason       = null
                        | planner_failed
                        | generator_failed
                        | evaluator_failed

    EpisodeManager.handle_attempt_closed(H)
```

`H.continuation_goal` is set when the planner submits its plan, not at close.
`submit_full_plan` leaves it null; `submit_partial_plan(continuation_goal)`
sets it to the supplied goal. On failure paths it remains null. Each harness
graph's `continuation_goal` belongs to that graph alone; later graphs in the
same segment do not inherit it from prior graphs.

`AttemptOrchestrator` does not inspect attempt budget and does not create the
next graph. Retry is a segment-level decision owned by `EpisodeManager`.

## Segment Reaction

`EpisodeManager` reacts to a closed harness graph. A passed graph always
closes its segment; a failed graph either retries within the segment or closes
the segment failed. The manager's only output is a `EpisodeClosureReport`:

```text
H.status:
  passed
    segment.continuation_goal = H.continuation_goal
    close current segment.

    if segment.continuation_goal is None:
      emit EpisodeClosureReport { outcome = terminal_success }
    else:
      emit EpisodeClosureReport { outcome = success_continue(segment.continuation_goal) }

  failed
    if current segment has attempt budget remaining:
      create Attempt sequence N+1 in the same segment.
    else:
      close current segment failed.
      emit EpisodeClosureReport { outcome = attempt_plan_failed(attempted_plan_history) }
```

A `EpisodeManager` retry creates a new `Attempt` in the same
`Episode`. The manager never creates a continuation segment, a new complex
task request, or another manager instance.

`attempt_plan_failed` is assembled from all harness graph summaries in the
closed segment, ordered by `attempt_sequence_no`. The payload must show the plan
each graph tried and the failure evidence for that graph; retry exhaustion is
only the condition that makes the segment close, not the semantic outcome.

There is no policy hook for "spend retry on a passed graph": once a graph
passes, it closes the segment. Plan quality is enforced by the evaluator's
pass/fail decision, not by the segment manager.

`MissionHandler` reacts to the `EpisodeClosureReport`:

- `terminal_success` or `attempt_plan_failed` -> close the mission request
  and return the close report to `requested_by_task_id`.
- `success_continue(goal)` -> create the next `Episode` with `goal` set,
  append it to `episode_ids`, spawn a fresh `EpisodeManager`, and let
  that manager create the next segment's initial harness graph.

## Closure decision tree

```text
Entry into the orchestrator is one of:
  - Terminal tool handler calls apply_plan_submission /
    apply_generator_submission / apply_evaluator_submission for H
    (drives the generating and evaluating close branches; planner success
     leaves the planning stage rather than closing the graph)
  - Runtime synthesizes a PlannerFailureSubmission and calls
    apply_planner_failure(submission)
    (drives the planning close branch only)
        |
        v
   H.stage:
        |
   +----+------------+----------------+
   v                 v                v
planning          generating       evaluating
   |                 |                |
   v                 v                v
planner ended      generators       evaluator submitted
without valid      quiescent?       success?
plan?              |                |
   |           +----+----+       +---+---+
   v           v         v       v       v
H failed     no        yes    H passed H failed
(planner_    |          |             (evaluator_failed)
 step_...)   v          v
          keep       any FAILED
          running    or BLOCKED?
                     |
                +----+----+
                v         v
             H failed  spawn evaluator
             (generator_failed)
```

## Implementation tasks

1. Add `AttemptOrchestrator` lookup by `Attempt.id` via
   `AttemptOrchestratorRegistry`.
2. Add the unified `apply_plan_submission(...)` entry on
   `AttemptOrchestrator` for `submit_full_plan` and
   `submit_partial_plan`. The entry persists the plan contract, creates
   generator task rows, advances stage to `generating`, then calls
   `_dispatch_ready_work()`.
3. Add `apply_generator_submission(...)` on `AttemptOrchestrator`. It
   writes the generator task row for either executor or verifier outcomes;
   on failure it blocks pending descendants. Calls `_dispatch_ready_work()`
   after the mutation.
4. Add `apply_evaluator_submission(...)` on `AttemptOrchestrator`. It marks
   the evaluator task done or failed and calls `_dispatch_ready_work()`, which
   then closes the graph.
5. Keep both legacy `submit_request_plan` and canonical
   `request_mission_solution` request-start handling out of
   `AttemptOrchestrator`. The Phase 04 spawn handler owns the transition
   to `waiting_mission`; Phase 02 only ensures the orchestrator observes
   that status as non-terminal during quiescence checks. The matching
   `apply_mission_close_report` resume entry ships in Phase 04.
6. Add private `_dispatch_ready_work()` and `_close_graph(...)` helpers.
   `_dispatch_ready_work()` launches dependency-free pending generators, waits
   for generator quiescence, spawns the evaluator when every generator is
   `done`, and closes the graph when the evaluator is terminal.
7. Add `apply_planner_failure(submission: PlannerFailureSubmission)` on the
   orchestrator façade. It marks the planner task `failed` with the
   runtime-supplied summary and delegates to
   `_close_graph(FAILED, planner_failed)`.
8. Implement graph close reporting via `_close_graph(...)` as the single close
   site: it calls `graph_store.close(...)` exactly once and then
   `on_graph_closed(graph_id)` to `EpisodeManager`.
9. Wire the orchestrator factory through
   `MissionHandler._spawn_segment_manager` to every spawned
   `EpisodeManager`.
10. Preserve Phase 01 continuation segment creation in
    `MissionHandler`; Phase 04 only wires final close-report delivery
    and delegated-request resume.

## Phase exit criteria

The behavioral spec lives in the sections above (responsibilities, stages,
failure escape valves, outcome, segment reaction). The canonical
criteria-to-test mapping is in
[Phase 02 - Implementation Plan §10](./phase-02-implementation-plan.md#10-phase-02-exit-criteria-mapping).
