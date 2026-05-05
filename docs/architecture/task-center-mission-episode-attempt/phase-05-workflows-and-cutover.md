# Phase 05 - End-to-End Workflows and Cutover

## Goal

Validate the full migration, remove obsolete graph-as-attempt behavior, and cut
callers over to the `Mission` plus `Episode` plus `Attempt`
model.

Partial-plan continuation remains part of the lifecycle. A mission request
has one or more ordered segments; retry is represented by multiple harness
graphs inside the current segment.

## Happy path

```text
requesting executor starts
    |
    v
requesting executor decides task is non-atomic
    |
    v
requesting executor calls request_mission_solution(goal)
    |
    v
MissionHandler creates Mission C1
  requested_by_task_id = requesting executor
    |
    v
MissionHandler creates Episode S1
  and spawns EpisodeManager(S1)
    |
    v
EpisodeManager(S1) creates Attempt S1.H1
    |
    v
AttemptOrchestrator(S1.H1) spawns planner
    |
    v
planner submits submit_full_plan with task_specification,
evaluation_criteria, tasks, and task_specs
    |
    v
S1.H1.continuation_goal = null
    |
    v
AttemptOrchestrator(S1.H1) instantiates generator DAG and spawns generators
    |
    v
executors and verifiers submit success
    |
    v
AttemptOrchestrator(S1.H1) spawns evaluator
    |
    v
evaluator submits success
    |
    v
AttemptOrchestrator(S1.H1) marks graph passed
    |
    v
EpisodeManager(S1) closes S1
S1.continuation_goal = S1.H1.continuation_goal = null
EpisodeManager(S1) emits EpisodeClosureReport { outcome = terminal_success }
    |
    v
MissionHandler closes C1 success
    |
    v
runtime delivers mission success report to requested_by_task_id
```

## Partial continuation path

```text
planner in S1.H1 submits submit_partial_plan with task_specification,
evaluation_criteria, tasks, task_specs, and continuation_goal = G
    |
    v
S1.H1.continuation_goal = G          (per-graph; not shared with later graphs)
    |
    v
generators complete partial DAG
    |
    v
evaluator submits success
    |
    v
AttemptOrchestrator(S1.H1) marks graph passed
    |
    v
EpisodeManager(S1) closes S1
S1.continuation_goal = S1.H1.continuation_goal = G
    (segment inherits from the passing harness graph)
EpisodeManager(S1) emits EpisodeClosureReport { outcome = success_continue(G) }
    |
    v
MissionHandler creates Episode S2 because outcome is success_continue
  goal = G
MissionHandler appends S2 to episode_ids
MissionHandler spawns EpisodeManager(S2)
    |
    v
EpisodeManager(S2) creates Attempt S2.H1
    |
    v
planner in S2.H1 may submit_full_plan or submit_partial_plan
  (same-request vertical continuation is allowed)
    |
    v
AttemptOrchestrator(S2.H1) runs graph to full-plan pass
S2.H1.continuation_goal = null
    |
    v
EpisodeManager(S2) closes S2 (S2.continuation_goal = null)
emits EpisodeClosureReport { outcome = terminal_success }
    |
    v
MissionHandler closes C1 and returns one final result to
requested_by_task_id
```

## Segment-manager retry then pass path

```text
planner in S1.H1 submits a full plan; generators run; evaluator fails
(or planner exhausts, or generator fails)
    |
    v
AttemptOrchestrator(S1.H1) marks graph failed
    |
    v
EpisodeManager decides attempt budget remains
EpisodeManager creates next Attempt S1.H2
    (S1.H2.continuation_goal starts unset; its own planner will decide)
    |
    v
planner in S1.H2 submits submit_full_plan or submit_partial_plan
    (independent decision; S1.H1's continuation_goal is not inherited)
    |
    v
S1.H2 runs to pass
    |
    v
EpisodeManager closes S1 successfully
S1.continuation_goal = S1.H2.continuation_goal
    (only the passing graph contributes)
```

## Resolver loop validation

The resolver loop remains inside one `Attempt`:

```text
verifier or evaluator calls ask_resolver(issues)
    |
    v
resolver runs and may edit
    |
    v
resolver returns resolved plus summaries
    |
    v
caller re-checks
    |
    +-- resolved true  -> may submit success
    |
    +-- resolved false -> unresolved count increments
                         at five unresolved calls, success terminal is blocked
                         caller must submit failure
```

## Failure workflow validation

### Generator failure

```text
generator in S1.H1 submits failure
    |
    v
dependent generators become BLOCKED
    |
    v
independent generators keep running
    |
    v
generators become quiescent
    |
    v
AttemptOrchestrator(S1.H1) marks graph failed with generator_failed
and reports failure to EpisodeManager
    |
    +-- EpisodeManager: attempt budget remains -> create next graph S1.H2
    |
    +-- EpisodeManager: retry exhausted      -> emit attempt_plan_failed(attempted_plan_history)
                                                    MissionHandler closes C1 failed
```

### Evaluator failure

```text
evaluator in S1.H1 submits failure
    |
    v
AttemptOrchestrator(S1.H1) marks graph failed with evaluator_failed
and reports failure to EpisodeManager
    |
    +-- EpisodeManager: attempt budget remains -> create next graph S1.H2
    |
    +-- EpisodeManager: retry exhausted      -> emit attempt_plan_failed(attempted_plan_history)
                                                    MissionHandler closes C1 failed
```

### Planner exhaustion

```text
planner in S1.H1 ends without valid full-plan submission
    |
    v
runtime reports planner_failed
    |
    v
AttemptOrchestrator(S1.H1) marks graph failed
and reports failure to EpisodeManager
    |
    +-- EpisodeManager: attempt budget remains -> create next graph S1.H2
    |
    +-- EpisodeManager: retry exhausted      -> emit attempt_plan_failed(attempted_plan_history)
                                                    MissionHandler closes C1 failed
```

## Cutover sequence

1. Add feature flags or compatibility adapters if needed so old tests can run
   while the new model lands.
2. Add `MissionHandler` for request creation and close-report
   delivery.
3. Migrate persistence and stores from graph-as-attempt to
   `Mission` / `Episode` / `Attempt`.
4. Migrate graph terminal handlers to `AttemptOrchestrator` routing and
   request decisions to `MissionHandler` plus segment decisions to
   `EpisodeManager`.
5. Migrate retry from attempt rows or child graph spawn to
   `EpisodeManager` creation of the next `Attempt` inside the same
   segment after a failed graph.
6. Migrate `submit_request_plan` to `request_mission_solution`.
7. Migrate partial-plan continuation to `Episode` creation with `goal`
   inherited from the passing harness graph's `continuation_goal`.
8. Migrate tool gates to read request, segment, and harness graph state.
9. Update prompts and docs that mention retry as a child graph or
   `RETRY_ON_FAILURE`.
10. Remove obsolete attempt rows, old graph attempt state, old spawn reasons, the
    obsolete persisted `plan_shape` field, old persisted
    `final_attempt_id` fields, `retry_after_partial`, and compatibility
    code.
11. Run targeted TaskCenter runtime tests, then broader backend checks.

The `final_attempt_id` in `EpisodeClosureReport` remains valid as an
event payload. The removal item above refers only to obsolete persisted fields
from the old graph-as-attempt model.

## Test plan

Prioritize focused tests near the runtime modules touched by the migration.

Minimum coverage:

- `request_mission_solution` creates `Mission`.
- The complex-task close report becomes the requesting executor task result.
- `MissionHandler` is the only creator and closer for requests, and
  the only creator of `Episode` records (initial and continuation).
- `EpisodeManager` is per-segment, the only creator of `Attempt`
  records inside its owned segment, and the only emitter of `EpisodeClosureReport`.
- Request links to `requested_by_task_id`.
- Initial `Episode` creation.
- Initial `Attempt` creation.
- Full-plan happy path.
- Generator failure quiescence.
- Evaluator failure triggers a `EpisodeManager` retry decision.
- Planner exhaustion triggers a `EpisodeManager` retry decision.
- Retry budget exhaustion.
- `EpisodeManager` retry creates `Attempt` N+1 inside the same segment.
- A later harness graph's `continuation_goal` is set independently by its own
  planner and is not inherited from prior failed graphs.
- Continuation creates `Episode` N+1 with `goal` inherited from the
  previous segment's `continuation_goal`, which itself was inherited from the
  passing harness graph that closed the previous segment.
- A passing harness graph always closes its segment; failed graphs return to
  `EpisodeManager` for a retry decision subject to budget.
- `request_mission_solution` can create a delegated `Mission` from
  a generator executor inside an existing harness graph.
- Partial-plan ancestor gate blocks child request planners only when a caller
  graph in their request ancestry was itself partial-planned.
- No `RETRY_ON_FAILURE` graph spawn remains.
- No `ROOT` spawn or creation reason remains.

Suggested commands:

```bash
uv run pytest backend/tests/test_task_center -q
uv run pytest backend/tests/test_engine -q
uv run ruff check backend/src backend/tests
uv run mypy --config-file backend/mypy.ini backend/src/task_center backend/src/agents
```

## Resolved design questions

### Retry budget lives on `Episode`, not `Mission`

`Mission` does **not** retry. The only retry primitive is a
`Episode` creating a new `Attempt` inside itself when the previous
graph fails and budget remains. The request closes failed when the *current*
segment exhausts its budget — it never re-issues a segment.

The budget is a fixed runtime default applied at segment creation:

- `HarnessLifecycleConfig.default_attempt_budget = 2` at
  `backend/src/task_center/config.py:16`.
- `MissionHandler` reads it for both the initial segment
  (`handler.py:98`) and each continuation segment (`handler.py:122`).
- `Episode.attempt_budget` (`segment/segment.py:31`) and
  `has_budget_remaining` (`segment/segment.py:48`) drive the
  `EpisodeManager` retry decision.

No per-request override is exposed. If a caller later needs one, threading
it through `Mission` is a small, additive change — out of scope
for this phase.

### Planner-exhaustion signal

Planner exhaustion = the planner agent terminates **without** having
submitted a valid `submit_full_plan` or `submit_partial_plan`. The runtime
detects this on agent exit and dispatches a `PlannerFailureSubmission` to
the orchestrator. `AttemptOrchestrator.apply_planner_failure`
(`orchestrator.py:162–190`) closes the graph with
`AttemptFailReason.PLANNER_FAILED`, which then flows into the standard
`EpisodeManager` retry-or-fail decision path.

This is already implemented; Phase 05's job is to exercise it in
`test_phase05_failure_paths.py`.

## Phase exit criteria

- All phase tests pass.
- Public executor contract exposes `request_mission_solution`,
  `submit_execution_success`, and `submit_execution_failure`.
- Docs no longer describe retry as `RETRY_ON_FAILURE` child graph creation.
- Segment progression reflects continuation through `continuation_goal`
  inherited from the passing harness graph.
- Retry history is derived from ordered harness graphs inside one segment, with
  per-graph `continuation_goal` independence.
