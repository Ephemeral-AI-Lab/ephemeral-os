# Phase 02 - Harness Graph Orchestrator Lifecycle

## Goal

Move single-harness-graph execution decisions into `HarnessGraphOrchestrator`.

The lifecycle split is:

- `ComplexTaskRequestHandler` owns the request boundary and the segment chain:
  `request_complex_task_solution`, request creation, initial-segment creation,
  continuation-segment creation, request close, and final close-report delivery
  to `requested_by_task_id`.
- `TaskSegmentManager` is per-`TaskSegment` and owns harness-graph transitions
  inside one segment: attempt-budget decisions, next-harness-graph creation after
  failed graphs, and segment close. It emits a `TaskSegmentClosureReport` to
  `ComplexTaskRequestHandler` when its segment closes.
- `HarnessGraphOrchestrator` owns one `HarnessGraph` execution:
  `planner -> generator tasks -> evaluator`.

`HarnessGraphOrchestrator` is in-process and ephemeral. Durable state lives on
`ComplexTaskRequest`, `TaskSegment`, `HarnessGraph`, tasks, and task outputs.

## Phase 01 inheritance

Phase 01 ships the orchestrator's contract surface and every persistence
seam this phase needs; Phase 02 only fills behaviour.

**Already in place:**

- `HarnessGraphOrchestrator` class skeleton at
  `backend/src/task_center/complex_task_request/segment/harness_graph/orchestrator.py`
  with constructor signature `(harness_graph, graph_store, on_graph_closed)`.
  Phase 02 should replace the old terminal-handler placeholder surface with
  `start()` and `reconcile()` entry points plus internal state-mutation and
  dispatch primitives.
- `HarnessGraphStore` mutators that the orchestrator drives stage-by-stage:
  `set_planner_task_id`, `set_plan_contract(task_specification,
  evaluation_criteria, continuation_goal)`, `set_generator_task_ids`,
  `set_evaluator_task_id`, `set_stage`, and `close(status, fail_reason,
  closed_at)`.
- `TaskSegmentManager.__init__` accepts an optional
  `orchestrator_factory: Callable[[HarnessGraph], HarnessGraphOrchestrator] | None`
  parameter (`None` in Phase 01); Phase 02 wires it.
- `TaskSegmentManager.handle_harness_graph_closed` is the callback the
  orchestrator's `on_graph_closed` already targets. The closure routing
  (`PASSED → terminal_success | success_continue`,
  `FAILED → retry or attempt_plan_failed`) is already verified by
  `backend/tests/task_center/lifecycle/test_integration_smoke.py`, which
  exercises the full pipeline through a synchronous stub orchestrator.
- Graph-level invariants
  (`assert_graph_running`, `assert_graph_sequence_contiguous`,
  `assert_fail_reason_present_on_failure`) live in
  `task_center.complex_task_request.segment.harness_graph.invariants` and
  raise `GraphInvariantViolation` on breach.

**Phase 02 wires:**

- The planner / generator / evaluator reconcile loop inside
  `HarnessGraphOrchestrator` (replacing the `NotImplementedError` stubs).
- A terminal-call view that terminal tools write after validation and that the
  orchestrator reads during reconciliation.
- An orchestrator factory passed to every `TaskSegmentManager` spawned by
  `ComplexTaskRequestHandler._spawn_segment_manager`.
- Calls to `graph_store.set_*` mutators as the orchestrator advances through
  `planning → generating → evaluating`, and the persisted close via
  `graph_store.close(...)` followed by the `on_graph_closed` callback.

## Responsibility Boundary

`HarnessGraphOrchestrator` never creates `ComplexTaskRequest`, `TaskSegment`, or
sibling `HarnessGraph` rows. It receives a current `HarnessGraph`, runs it to a
passed or failed outcome, and reports that outcome to its owning
`TaskSegmentManager`.

Terminal tools also remain outside the orchestrator. They parse public tool
input, enforce role gates, return user-facing tool errors, persist an accepted
terminal-call view, and end the agent run. The orchestrator reads that
terminal-call view later and owns only the resulting state transition and
follow-up task dispatch.

`TaskSegmentManager` then decides, inside its owned segment, whether to:

- create another `HarnessGraph` after a failed graph when attempt budget remains,
- close the current segment and emit a `TaskSegmentClosureReport`.

`TaskSegmentManager` never creates a continuation `TaskSegment`. When a passing
graph closes the segment with a non-null `continuation_goal`, the manager emits
`success_continue(goal)` and `ComplexTaskRequestHandler` creates the next
segment and its fresh `TaskSegmentManager`.

`ComplexTaskRequestHandler` closes the `ComplexTaskRequest` and delivers the
final report when it receives a `TaskSegmentClosureReport` with
`terminal_success` or `attempt_plan_failed`.

## Harness Graph Orchestrator Responsibilities

For one `HarnessGraph`, `HarnessGraphOrchestrator`:

1. Opens one task creation / task mutation service surface for accepted
   terminal submissions and nested complex-task close reports.
2. Opens one task dispatch / graph-outcome surface that decides when tasks can
   launch and when graph state should be escalated to the owning segment.

Graph close is a state mutation followed by the existing
`on_graph_closed(graph_id)` callback to `TaskSegmentManager`.

### Task creation / task mutation handler

This handler is an internal service/function-call surface owned by
`HarnessGraphOrchestrator`. It is not a public terminal tool API. Public tool
handlers still parse input, enforce role gates, persist an accepted call view,
and end the agent run. The orchestrator's mutation handler consumes the accepted
view later.

The handler has four entry families:

| Entry | Consumes | Mutates |
| ----- | -------- | ------- |
| `apply_plan_submission(...)` | `submit_full_plan` or `submit_partial_plan` | Persists graph contract, stores `continuation_goal` for partial plans, creates all generator task rows, sets graph stage to `generating`. |
| `apply_executor_submission(...)` | `submit_execution_success` or `submit_execution_failure` | Appends executor summary and marks the generator task `done` or `failed`. |
| `apply_verifier_submission(...)` | `submit_verification_success` or `submit_verification_failure` | Appends verifier summary and marks the generator task `done` or `failed`. |
| `apply_evaluator_submission(...)` | `submit_evaluation_success` or `submit_evaluation_failure` | Appends evaluator summary and closes the graph passed or failed. |

`apply_plan_submission(...)` is the unified planner entry. It receives a
normalized plan view with `kind = full | partial`; it does not expose separate
orchestrator methods for `submit_full_plan` and `submit_partial_plan`.

Executor and verifier submissions are both generator task outcomes, but they
remain separate mutation entries because their public tool contracts, summaries,
and gating rules differ.

The mutation handler must not handle generator `submit_request_plan` or any
legacy request-plan tool. Request-style generator handoffs are not success or
failure task outcomes. The supported handoff path is
`request_complex_task_solution`, which belongs to the complex-task request
boundary described in Phase 04. If a legacy `submit_request_plan` surface still
exists during migration, it should be rejected or translated before reaching
`HarnessGraphOrchestrator`.

### Task dispatch / graph-outcome manager

This manager is also internal to one `HarnessGraphOrchestrator`. It reads
persisted task state after mutations and decides the next launch or graph
outcome.

It owns:

- launching the planner when a graph starts,
- launching dependency-free generator tasks after a plan is accepted,
- launching generator tasks whose dependencies are all `DONE`,
- launching the evaluator only after every generator task is `DONE`,
- blocking pending descendants after a generator failure,
- holding graph failure escalation until generator quiescence,
- closing the graph and notifying `TaskSegmentManager` when the graph outcome
  is known.

Generator failures include executor and verifier failures. When an executor
task fails, the manager marks descendants that can no longer run as `BLOCKED`,
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
`DONE`, `HarnessGraphOrchestrator` creates the evaluator and moves to
`evaluating`. If any generator is `FAILED` or `BLOCKED`, the graph closes as
failed after generator quiescence.

`request_complex_task_solution` is a generator task handoff. The requesting
generator agent run exits after the request tool call; the outer graph receives
that task's final result when the nested complex task request closes.

## Failure escape valves

```text
Failure escape valves:
  - Tool-call-level error from any agent
      prehook or handler returns ToolResult(is_error=True)
      -> agent retries inside its own run
      -> no harness-graph-level escalation

  - Generator executor/verifier submit_*_failure terminal-call view
      -> wait for generator quiescence
      -> mark HarnessGraph failed with generator_failed

  - Generator submit_request_plan or legacy request-plan call
      -> not accepted by HarnessGraphOrchestrator
      -> reject/translate before mutation handling

  - Evaluator submit_evaluation_failure terminal-call view
      -> mark HarnessGraph failed with evaluator_failed immediately

  - Planner agent ends without a successful submit_*_plan
      -> runtime marks HarnessGraph failed with planner_step_budget_exhausted
```

The planner has no failure terminal. Its only soft-fail channel is inline
tool-call rejection. Only a planner run ending without a valid plan
submission escalates to `HarnessGraphOrchestrator` as
`planner_step_budget_exhausted`.

## Harness Graph Failures

| Failure mode | Detected by | Wait point |
| ------------ | ----------- | ---------- |
| `planner_step_budget_exhausted` | runtime ends planner without valid plan submission | immediate |
| `generator_failed` | generator submitted failure | wait until every generator is `DONE`, `FAILED`, or `BLOCKED` |
| `evaluator_failed` | evaluator submitted `submit_evaluation_failure` | immediate |

### Generator-failure quiescence

- When a generator fails, its dependents transition to `BLOCKED`.
- Independent sibling generators keep running.
- `HarnessGraphOrchestrator` does not retry mid-flight.
- After all generators are in `DONE`, `FAILED`, or `BLOCKED`,
  `HarnessGraphOrchestrator` makes one harness-graph-level outcome decision.
- If `TaskSegmentManager` spends attempt budget, it creates the next harness
  graph; that graph's planner receives the whole failure landscape through the
  context engine.

### Evaluator failure

The evaluator is spawned only after every generator is `DONE`, so quiescence is
already satisfied. Evaluator failure triggers harness graph failure immediately.

## Harness Graph Outcome

```text
close_harness_graph(H, outcome):
    H.status            = passed | failed
    H.stage             = closed
    H.continuation_goal = null
                        | string (set from submit_partial_plan)
    H.fail_reason       = null
                        | planner_step_budget_exhausted
                        | generator_failed
                        | evaluator_failed

    TaskSegmentManager.handle_harness_graph_closed(H)
```

`H.continuation_goal` is set when the planner submits its plan, not at close.
`submit_full_plan` leaves it null; `submit_partial_plan(continuation_goal)`
sets it to the supplied goal. On failure paths it remains null. Each harness
graph's `continuation_goal` belongs to that graph alone; later graphs in the
same segment do not inherit it from prior graphs.

`HarnessGraphOrchestrator` does not inspect attempt budget and does not create the
next graph. Retry is a segment-level decision owned by `TaskSegmentManager`.

## Segment Reaction

`TaskSegmentManager` reacts to a closed harness graph. A passed graph always
closes its segment; a failed graph either retries within the segment or closes
the segment failed. The manager's only output is a `TaskSegmentClosureReport`:

```text
H.status:
  passed
    segment.continuation_goal = H.continuation_goal
    close current segment.

    if segment.continuation_goal is None:
      emit TaskSegmentClosureReport { outcome = terminal_success }
    else:
      emit TaskSegmentClosureReport { outcome = success_continue(segment.continuation_goal) }

  failed
    if current segment has attempt budget remaining:
      create HarnessGraph sequence N+1 in the same segment.
    else:
      close current segment failed.
      emit TaskSegmentClosureReport { outcome = attempt_plan_failed(attempted_plan_history) }
```

A `TaskSegmentManager` retry creates a new `HarnessGraph` in the same
`TaskSegment`. The manager never creates a continuation segment, a new complex
task request, or another manager instance.

`attempt_plan_failed` is assembled from all harness graph summaries in the
closed segment, ordered by `graph_sequence_no`. The payload must show the plan
each graph tried and the failure evidence for that graph; retry exhaustion is
only the condition that makes the segment close, not the semantic outcome.

There is no policy hook for "spend retry on a passed graph": once a graph
passes, it closes the segment. Plan quality is enforced by the evaluator's
pass/fail decision, not by the segment manager.

`ComplexTaskRequestHandler` reacts to the `TaskSegmentClosureReport`:

- `terminal_success` or `attempt_plan_failed` -> close the complex task request
  and return the close report to `requested_by_task_id`.
- `success_continue(goal)` -> create the next `TaskSegment` with `goal` set,
  append it to `task_segment_ids`, spawn a fresh `TaskSegmentManager`, and let
  that manager create the next segment's initial harness graph.

## Closure decision tree

```text
HarnessGraphOrchestrator reconciles a terminal-call view for HarnessGraph H
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

1. Add `HarnessGraphOrchestrator` lookup by `HarnessGraph.id`.
2. Add the terminal-call view and reader used by tool handlers and tests.
3. Add the task creation / mutation handler with one unified
   `apply_plan_submission(...)` entry for `submit_full_plan` and
   `submit_partial_plan`.
4. Add executor and verifier task-outcome entries for `submit_*_success` and
   `submit_*_failure`.
5. Keep generator `submit_request_plan` out of the orchestrator mutation
   surface.
6. Add the task dispatch / graph-outcome manager for launchability,
   generator-failure quiescence, evaluator spawn, and graph close escalation.
7. Implement planner success path: valid plan submission creates generator
   tasks and task dependencies for the current `HarnessGraph`.
8. Implement planner exhaustion path.
9. Implement generator failure quiescence and dependent blocking.
10. Implement generator complex-task handoff and nested close-report resume.
11. Implement evaluator spawn after generator success.
12. Implement evaluator success and failure reconciliation.
13. Implement graph close reporting from `HarnessGraphOrchestrator` to
   `TaskSegmentManager`.
14. Keep continuation segment creation stubbed or feature-gated until Phase 04.

## Phase exit criteria

- A harness graph can complete a full-plan execution successfully.
- Generator failure waits for quiescence before graph failure is reported.
- Evaluator failure closes the harness graph immediately.
- Planner exhaustion closes the harness graph with
  `planner_step_budget_exhausted`.
- `submit_full_plan` and `submit_partial_plan` share one orchestrator plan
  mutation entry.
- Executor and verifier success/failure submissions update generator task
  summaries and statuses without leaking public tool handlers into graph
  dispatch policy.
- Generator `submit_request_plan` is not handled by
  `HarnessGraphOrchestrator`.
- No retry path is implemented inside `HarnessGraphOrchestrator`; retry is
  delegated to `TaskSegmentManager`.
