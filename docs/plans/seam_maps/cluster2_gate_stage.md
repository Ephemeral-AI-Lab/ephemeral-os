# Seam Map — Cluster 2: Gate / Two Tuples / Stage Collapse (WS2)

**Scope:** Replace `evaluation_criteria` with a first-class `reducers` plan-task list;
add `reducer_task_ids` as a SECOND tuple alongside `generator_task_ids`; collapse stages
`PLAN/GENERATE/EVALUATE/CLOSED` → `PLAN/RUN/CLOSED`; schedule generators + reducers as one
DAG over the union of both tuples; gate: all plan tasks DONE → PASSED, any failed/blocked →
FAILED(`TASK_FAILED`).

**Plan source:** `docs/plans/reducers_outcomes_redesign_PLAN.md` §7 WS2, §2 class ref, §4 vocab,
§8 D4/D6, §9 invariants, §10 gate scenarios.

**Hard cross-WS dependencies (this cluster CANNOT land standalone):**
- **WS1** provides: `TaskCenterTaskRole.REDUCER`, `reducer_task_id(attempt_id, local_id)`
  primitive (`:red:<local_id>`), `AgentLaunchFactory.for_reducer(...)`, `recipes/reducer.py`,
  `ReducerSubmission` import path, the `submit_reduction_success/failure` terminals. WS2 USES
  all of these; do not author them here. If WS1 hasn't landed, stub `for_reducer` +
  `reducer_task_id` to make WS2 compile, but flag it.
- **WS6/D5** drops `Task.task_center_attempt_id`. This is **load-bearing for WS2** — see the
  CRITICAL section below. The two query methods WS2 currently leans on
  (`list_generator_tasks_for_attempt`, `list_tasks_for_attempt`) both filter on that exact
  column and will not survive. WS2's RUN scheduler must source plan-task rows **by id from the
  union of the two tuples**, not by an attempt-id query. Coordinate or assume D5.
- **WS7** owns `apply_workflow_closure_report`, `_build_handoff_rollup`,
  `child_outcomes_for_workflow`, the `WorkflowClosureReport` import in `orchestrator.py` and
  `orchestrator_registry.py`. WS2 must NOT delete these. Their presence collides with WS2's
  `apply_reducer_submission` edit only at the import block — note, don't touch.

---

## CRITICAL: RUN-stage task sourcing (the seam the plan compresses)

The plan says "`_advance_generator_stage` → `_advance_run_stage` over the union of both
tuples; reuse `ready_pending_*` + `dag_status`." Made concrete:

1. **Today** `_advance_generator_stage` calls
   `runtime.task_store.list_generator_tasks_for_attempt(attempt.id)`
   (`task_center_store.py:205`, filters `role=="generator"` AND
   `task_center_attempt_id==attempt_id`). Under WS6/D5 the `task_center_attempt_id` column is
   GONE, so that query method (and `list_tasks_for_attempt:191`) cannot exist.
2. **Target:** `_advance_run_stage` builds the plan-task record list from
   `attempt.generator_task_ids + attempt.reducer_task_ids` by fetching each row via
   `runtime.task_store.get_task(tid)` (single-get exists at `task_center_store.py:177` /
   protocol `persistence.py:189`). This **naturally excludes the planner task** (it is in
   neither tuple), so the union list is exactly the DAG.
3. **No batch-get exists.** Either loop `get_task` (simplest, N small) OR add
   `list_tasks_by_ids(ids)` to `TaskCenterTaskStore` + the `persistence.py` protocol. RECOMMEND
   the loop for minimality; flag `list_tasks_by_ids` as the alternative.
4. **Both `ready_pending_*` and `dag_status` are PURE over whatever record list they receive.**
   No logic change inside them for the union — they already key off `needs` + `status`. Pass
   them the union list. (See plan_dag.py section: `dag_status` is a pure rename.)

If WS6 has NOT yet dropped the column, the loop-`get_task` approach still works (it never reads
`task_center_attempt_id`), so writing WS2 against id-sourcing is forward-safe either way.

---

## File: `backend/src/task_center/attempt/state.py`  — CORE

Current (verified):
- `AttemptStage(StrEnum)`: `PLAN="plan"`, `GENERATE="generate"`, `EVALUATE="evaluate"`,
  `CLOSED="closed"` (`:10-14`).
- `AttemptStatus(StrEnum)`: `RUNNING/PASSED/FAILED` (`:17-20`) — UNCHANGED.
- `AttemptFailReason(StrEnum)`: `PLANNER_FAILED`, `GENERATOR_FAILED`, `EVALUATOR_FAILED`,
  `STARTUP_FAILED` (`:23-27`).
- `Attempt` dataclass (`:30-52`) fields incl. `plan_spec`, `evaluation_criteria: tuple[str,...]`,
  `generator_task_ids: tuple[str,...]`, `evaluator_task_id: str|None`. `is_closed` =
  `stage == AttemptStage.CLOSED` (`:50-52`).

Target:
- `AttemptStage`: drop `EVALUATE`; keep `PLAN="plan"`, `RUN="run"` (rename `GENERATE`→`RUN`,
  value `"generate"`→`"run"`), `CLOSED="closed"`.
- `AttemptFailReason`: replace the three role-specific values with `TASK_FAILED="task_failed"`,
  keep `STARTUP_FAILED="startup_failed"`. (Per §4: role of the failed task says which.)
- `Attempt`: drop `evaluator_task_id`; ADD `reducer_task_ids: tuple[str,...]`. Keep
  `generator_task_ids`. **KEEP `plan_spec` + `evaluation_criteria` field-wise for now? NO** —
  WS2 removes `evaluation_criteria` (it is replaced by `reducers`/`reducer_task_ids`). KEEP
  `plan_spec` (D3/M2 gate — see open_decisions). So final Attempt fields: drop
  `evaluation_criteria`, drop `evaluator_task_id`, add `reducer_task_ids`; `plan_spec` stays.
- `is_closed` unchanged.

NOTE on file location: WS6/D11 moves this whole module into `_core/state.py`. WS2 edits the
CONTENT here; if WS6 lands first, apply the same content edits at `_core/state.py`. Keep WS2
content-correct regardless of which file it lives in.

Risk: every importer of `AttemptStage.GENERATE`/`EVALUATE`, `evaluator_task_id`,
`AttemptFailReason.{PLANNER,GENERATOR,EVALUATOR}_FAILED`, `evaluation_criteria` breaks at
import/attr time. Enumerated below.

---

## File: `backend/src/task_center/_core/primitives.py`  — CORE (shared w/ WS1)

Current: `evaluator_task_id(attempt_id) -> f"{attempt_id}:evaluator"` (`:32-33`), exported in
`__all__` (`:53`).

Target (WS1 owns the ADD of `reducer_task_id`; WS2 owns the DROP of `evaluator_task_id`):
- Remove `evaluator_task_id` fn + its `__all__` entry.
- Confirm WS1 added `reducer_task_id(attempt_id, local_id) -> f"{attempt_id}:red:{local_id}"`
  (mirrors `generator_task_id`'s `:gen:` at `:28-29`). WS2 consumes it in the persist path.

Risk: `evaluator_task_id` imported in `stage_advancer.py:31`. That import line is deleted with
the stage rewrite.

---

## File: `backend/src/tools/submission/planner/_schemas.py`  — CORE

Current (verified, line numbers differ slightly from plan's `:31/:61/:70-75`):
- `PlanTaskInput` (`:26-48`): `id`, `agent_name`, `deps: list[str]` (`:31`) + `_validate_deps`
  (`:43-48`).
- `SharedPlannerSubmissionInput` (`:51-83`): `plan_spec`, `evaluation_criteria: list[str]
  Field(...,min_length=1)` (`:61`) + `_validate_evaluation_criteria` (`:70-75`), `tasks`,
  `task_specs`.
- `validate_nonblank` (`:86-89`).
- `build_planner_submission(...)` (`:104-170`): takes `evaluation_criteria`, builds
  `PlannedGeneratorTask(... deps=tuple(task.deps) ...)`, calls `ordered_generator_tasks(planned)`
  (`:149`), passes `evaluation_criteria=tuple(evaluation_criteria)` to `PlannerSubmission`
  (`:164`).

Target:
- `PlanTaskInput.deps` → `needs` (`:31`); `_validate_deps` → `_validate_needs` (rename fn +
  `@field_validator("needs")` + nonblank loop label).
- NEW `class ReducerInput(BaseModel, extra="forbid")`: `id: str Field(...,min_length=1)`,
  `needs: list[str] = Field(default_factory=list)`, `prompt: str Field(...,min_length=1)`.
  Validators: reuse `validate_nonblank` for `id` + `prompt` (D6: prompt required + nonblank),
  and the same needs-loop as PlanTaskInput. (Plan's "reuse the `:70-75` validator" = reuse the
  `validate_nonblank` helper for the prompt-nonblank check.)
- `SharedPlannerSubmissionInput`: replace `evaluation_criteria: list[str]` + its validator with
  `reducers: list[ReducerInput] = Field(..., min_length=1)`.
- `build_planner_submission`: param `evaluation_criteria: list[str]` → `reducers:
  list[ReducerInput]`. Build `PlannedGeneratorTask(... needs=tuple(task.needs) ...)`. Build a
  new `PlannedReducerTask` tuple (DTO from submissions.py; see open_decisions for name):
  `PlannedReducerTask(local_id=r.id, needs=tuple(r.needs), prompt=r.prompt)`.
- Call `ordered_plan_tasks(generators=planned_gen, reducers=planned_red)` (renamed from
  `ordered_generator_tasks`; new two-arg signature — see open_decisions). The validator now
  enforces: unique ids ACROSS both lists, known needs (a need may point at a generator OR a
  reducer), no cycles, **≥1 reducer**, **reachability** (every generator transitively in the
  `needs`-closure of ≥1 reducer).
- Pass `reducers=<ordered reducers tuple>` (not `evaluation_criteria`) to `PlannerSubmission`.
- The `_is_generator_capable_agent` gate (`:92-101`) — its docstring mentions "executor /
  verifier" and "evaluator"; WS3 removes verifier, WS1 the evaluator vocab. WS2 leaves the
  FUNCTION (still gates generator agent_names) but the docstring is propagation only.
- Error-string mapping in the `try/except TaskCenterInvariantViolation` (`:150-156`): today
  matches `"unknown deps"` and `"dependency cycle"`. After rename these messages change
  (`ordered_plan_tasks` raises new text incl. reachability + no-reducer). Update the substring
  matches to the new messages, OR (simpler) return `str(exc)` for all and let the new validator
  own user-facing wording. RECOMMEND: pass through new messages for the new rules
  (no-reducer / unreachable), keep cycle/unknown-needs friendly strings.

Risk: this file is the planner-tool boundary; its validation messages are asserted by
`backend/tests/unit_test/test_tools/test_submission_planner_tools.py` and
`scenarios/planner_validation/duplicate_local_id.py`. Those are propagation (string-match) but
listed below.

---

## File: `backend/src/task_center/submissions.py`  — CORE

Current (verified):
- `PlannedGeneratorTask` (`:15-23`): `local_id, agent_name, deps: tuple[str,...], task_spec`.
- `PlannerSubmission` (`:25-37`): `... plan_spec, evaluation_criteria: tuple[str,...], tasks:
  tuple[PlannedGeneratorTask,...], deferred_goal_for_next_iteration, summary`.
- `GeneratorSubmission` (`:49-57`): `outcome: Literal["success","failure","blocker"]`.
- `EvaluatorSubmission` (`:60-68`): `outcome: Literal["success","failure"]`.

Target:
- `PlannedGeneratorTask.deps` → `needs: tuple[str,...]`.
- NEW `@dataclass(frozen, slots) PlannedReducerTask`: `local_id: str`, `needs: tuple[str,...]`,
  `prompt: str`. (No `agent_name` — reducer's agent is the fixed reducer profile, resolved at
  launch via WS1's `REDUCER_AGENT_NAME`.)
- `PlannerSubmission`: replace `evaluation_criteria: tuple[str,...]` with
  `reducers: tuple[PlannedReducerTask,...]`. KEEP `plan_spec` (D3 gate). `tasks` field name
  unchanged (Tier-2 defers `tasks`→`generators`).
- `EvaluatorSubmission` → `ReducerSubmission`: rename class. WS1's §4 says reducer status is
  binary; align field with the plan's §2 `ReducerSubmission: status: Literal["success","failure"]`.
  **Field name decision:** WS4 renames `GeneratorSubmission.outcome` → `status`. WS2 may either
  (a) keep `outcome` on `ReducerSubmission` to match the still-`outcome` `GeneratorSubmission`
  and let WS4 rename both, or (b) rename to `status` now. RECOMMEND (a): keep `outcome:
  Literal["success","failure"]` so WS2 stays mechanical and WS4 does the unified `outcome→status`
  in one pass. Flag in open_decisions.

Risk: `PlannerSubmission` constructed in `_schemas.py:159` and consumed in
`orchestrator.apply_plan_submission` + `_persist_plan_contract` + `_persist_generator_tasks`.
`EvaluatorSubmission` constructed in the two evaluator terminal tools (WS1 moves those to
reducer/). The package `__init__.py` lazy-exports `EvaluatorSubmission` (`:62,100`) and
`PlannedGeneratorTask` (`:64,107`) — see `__init__.py` section.

---

## File: `backend/src/task_center/attempt/generator_dag.py` → `plan_dag.py`  — CORE

Current (verified): module owns `ordered_generator_tasks` (`:18-62`, validates unique ids,
known deps, no cycle via Kahn), `dependency_task_ids` (`:65-70`, maps locals via
`generator_task_id`), `_task_statuses_by_id` (`:73`), `ready_pending_generator_ids`
(`:79-89`), `GeneratorDagSummary` dataclass (`:92-96`), `_validate_persisted_deps` (`:102`),
`_unreachable_pending_ids` (`:114-153`), `summarize_generator_dag` (`:156-175`).

Target (module renamed to `plan_dag.py` per §3/§4):
1. **`ordered_generator_tasks` → `ordered_plan_tasks`** — NEW behavior, the heart of the gate
   invariants (§9). Signature: `(generators: tuple[PlannedGeneratorTask,...], reducers:
   tuple[PlannedReducerTask,...]) -> tuple[tuple[PlannedGeneratorTask,...],
   tuple[PlannedReducerTask,...]]` (returns both, topo-ordered; see open_decisions for the
   shape). Logic:
   - Build combined id set across generators+reducers; duplicate id ACROSS either → raise.
   - Build combined `needs` adjacency; a need may target any id (gen or reducer). Unknown need →
     raise (message keeps `"unknown deps"`-ish substring? NO — rename to `unknown needs` but then
     update the `_schemas.py` substring match; OR keep wording. Coordinate with `_schemas.py`).
   - Kahn topo over the combined graph; cycle → raise.
   - **≥1 reducer:** `if not reducers: raise TaskCenterInvariantViolation("Plan must contain at
     least one reducer")` (mirror `empty_tasks.py` rejection style — §10).
   - **Reachability:** compute the set of all task ids that are in the transitive `needs`-closure
     of ANY reducer (reverse-reachability: start from each reducer, walk `needs` edges). Every
     GENERATOR id must be in that set. Any generator not transitively needed by a reducer →
     raise `"unreachable generator(s): {...}"`. (A generator no reducer needs would reach DONE
     unjudged — §1.) Reducers themselves don't need to be reachable from anything.
   - Return both ordered tuples (split back out by type, preserving topo order within each).
2. **`dependency_task_ids` (`:65-70`)** — CRITICAL hand-edit. Today maps every local dep via
   `generator_task_id(attempt_id, dep)`. Now a `needs` entry can be a reducer local id → must map
   via `reducer_task_id`. **The persist path must build one `local_id → persisted_task_id` map
   across BOTH generators and reducers, then resolve each task's `needs` through that map.** Either
   (a) replace `dependency_task_ids` with a function taking the full local→id map, or (b) the
   orchestrator builds the map inline and `dependency_task_ids` is deleted. RECOMMEND (b): move
   resolution into `orchestrator._persist_*` where both tuples are in scope (see orchestrator
   section). This is the trickiest edit — two id namespaces, one needs-resolution map.
3. **`ready_pending_generator_ids` → `ready_pending_plan_ids`** (or `ready_pending_task_ids`) —
   logic UNCHANGED (keys off `needs`+`status`, role-agnostic). Pure rename + the union record
   list is passed in. (open_decisions: final name.)
4. **`GeneratorDagSummary` → `DagStatus`**, **`summarize_generator_dag` → `dag_status`** (§4 D15).
   Logic UNCHANGED — already role-agnostic over the record list. Pure rename.
5. `_validate_persisted_deps`, `_unreachable_pending_ids`, `_task_statuses_by_id`,
   `_FAILED_OR_BLOCKED`, `TERMINAL_GENERATOR_STATUSES` usage — UNCHANGED (operate on `needs` +
   status). Internal-message wording mentions "Generator task" — propagation-only cosmetic.

Risk: imported from `__init__.py:32,127-130` (`ordered_generator_tasks`), `_schemas.py:14,149`,
`orchestrator.py:31-34`, `stage_advancer.py:27-30`, and tests
`test_generator_dag.py`, `test_submission_planner_tools.py`. All repointed.

---

## File: `backend/src/task_center/attempt/stage_advancer.py` → `run_stage.py`  — CORE (heaviest)

Current (verified): `AttemptStageAdvancer` with `advance_ready_tasks` dispatching on stage
(`:61-69`): `GENERATE`→`_advance_generator_stage`, `EVALUATE`→`_advance_evaluator_stage`.
- `_advance_generator_stage` (`:71-99`): lists generator tasks, `ready_pending_generator_ids`,
  launches ready, then `summarize_generator_dag` → if `any_failed_or_blocked` close
  FAILED/GENERATOR_FAILED, elif `all_done` → `_start_evaluator_stage`.
- `_advance_evaluator_stage` (`:101-119`): reads `attempt.evaluator_task_id`, on task DONE →
  close PASSED, on FAILED → close FAILED/EVALUATOR_FAILED.
- `_launch_ready_generator` (`:140-178`), `_launch_evaluator` (`:180-201`),
  `_start_evaluator_stage` (`:203-265`, upserts evaluator task role EVALUATOR, sets
  evaluator_task_id, sets stage EVALUATE, calls `for_evaluator`).
- `_mark_launch_failed` (`:121-138`).

Target (`run_stage.py`, class may stay `AttemptStageAdvancer` or rename — open_decisions):
- `advance_ready_tasks` (`:61-69`): single branch — `if attempt.stage == AttemptStage.RUN:
  self._advance_run_stage(attempt)`. PLAN/CLOSED no-ops.
- **DELETE** `_advance_evaluator_stage`, `_start_evaluator_stage`, `_launch_evaluator`,
  the `for_evaluator`/`evaluator_task_id` imports. Reducers are persisted PENDING at
  plan-submit time and launched by the RUN loop dispatching on role — there is no bespoke
  evaluator stage.
- **`_advance_generator_stage` → `_advance_run_stage(attempt)`:**
  - Source the plan-task records by id from `attempt.generator_task_ids +
    attempt.reducer_task_ids` (see CRITICAL section — `get_task` loop, NOT
    `list_generator_tasks_for_attempt`).
  - `ready_ids = ready_pending_plan_ids(records)`. For each ready id, dispatch launch on the
    task row's `role`: `GENERATOR` → `_launch_ready_generator` (existing, calls `for_generator`);
    `REDUCER` → a new `_launch_ready_reducer` (mirror of `_launch_ready_generator` but calls
    WS1's `AgentLaunchFactory.for_reducer(attempt=attempt, task=task)`). Both flip PENDING→RUNNING
    via `set_task_status` and emit `task_ready`/`task_launched` audit.
  - After launching ready tasks (with the same `launch_failed → re-advance` retry as today),
    compute `state = dag_status(records)`. If `not state.all_quiescent`: return. If
    `state.any_failed_or_blocked`: `self._close_attempt(AttemptStatus.FAILED,
    AttemptFailReason.TASK_FAILED)`. Elif `state.all_done`:
    `self._close_attempt(AttemptStatus.PASSED, None)`. **This is the gate** — §1: all plan tasks
    DONE → PASSED; any failed/blocked → FAILED(TASK_FAILED). No separate "start evaluator" step.
- `_mark_launch_failed` (`:121-138`): the `summary={"fail_reason":"agent_launch_failed",...}`
  payload — WS4 renames the summary write to `outcomes`+`terminal_tool_result`; WS2 keeps the
  call shape, role arg becomes generic ("Generator"/"Reducer"). Launch-failure of a generator
  during RUN currently relies on `summarize` seeing it FAILED on the next `advance` (the
  `launch_failed → advance_ready_tasks()` recursion). Preserve that.
- The reducer's `needs` = its persisted `needs` (set at plan-submit). The RUN loop's
  `ready_pending_plan_ids` already gates a reducer until all its needed generators (and any
  needed reducers) are DONE — this is the reachability/judgement guarantee at runtime.

NOTE: this file imports `AttemptStage`, `AttemptFailReason`, `AttemptStatus` from
`attempt.state` (`:16-21`), `AgentLaunch`/`AttemptDeps` from `attempt.deps` (`:22-25` — WS6
moves deps), `ready_pending_generator_ids`+`summarize_generator_dag` from `generator_dag`
(`:27-30` — repoint to `plan_dag`), `evaluator_task_id` (`:31` — DELETE), `SpawnReason`
(`:32-36` — D5 removes; DELETE). Coordinate the deps/SpawnReason import with WS6/WS1.

Risk: heaviest hand-edit. The persist-at-plan-submit of reducers (orchestrator) must run BEFORE
RUN can schedule them. Verify reducer rows exist PENDING with role REDUCER and resolved `needs`.

---

## File: `backend/src/task_center/attempt/orchestrator.py`  — CORE

Current (verified): imports `assert_evaluator_task_for_submission` (`:14`),
`EvaluatorSubmission` (`:50`), `AttemptStage`/`AttemptFailReason` (`:37-42`), `SpawnReason`
(`:44-48`), `ordered_generator_tasks`+`dependency_task_ids` (`:31-34`). Methods:
- `apply_plan_submission` (`:116-141`): sets planner DONE, `_persist_plan_contract`,
  `_persist_generator_tasks(submission.tasks)`, `set_generator_task_ids`,
  `set_stage(AttemptStage.GENERATE)`, advance.
- `apply_planner_failure` (`:143-154`): closes FAILED with `AttemptFailReason.PLANNER_FAILED`.
- `apply_evaluator_submission` (`:161-164`): `_assert_submission_attempt` + `_mark_evaluator` +
  advance.
- `_persist_plan_contract` (`:257-263`): calls `set_plan_contract(... evaluation_criteria=...)`.
- `_persist_generator_tasks` (`:265-290`): `ordered_generator_tasks`, per task
  `generator_task_id` + `dependency_task_ids(... local_deps=task.deps)`, `upsert_task(... role
  GENERATOR ... task_center_attempt_id ... spawn_reason ...)`.
- `_mark_evaluator` (`:307-325`): asserts stage EVALUATE, matches `attempt.evaluator_task_id`,
  `assert_evaluator_task_for_submission`, `_write_submission_status(role="Evaluator", ...)`.
- `_write_submission_status` (`:327-349`): maps outcome→status (success→DONE, blocker→BLOCKED,
  else FAILED).

Target:
- `apply_plan_submission`: after persisting generators, also persist reducers
  (`_persist_reducer_tasks(submission.reducers)`), set BOTH tuples
  (`set_generator_task_ids` + new `set_reducer_task_ids`), `set_stage(AttemptStage.RUN)`,
  advance. **Persist reducers BEFORE setting stage to RUN** so the RUN scheduler sees them.
- `apply_planner_failure`: `AttemptFailReason.PLANNER_FAILED` → `TASK_FAILED` (planner is a task;
  §4 collapses to TASK_FAILED). (Note: plan §2 says planner is off-spine/control-plane; a planner
  failure is still a task failure → `TASK_FAILED` is the correct collapsed value. The
  `_mark_startup_failed` path uses `STARTUP_FAILED` and is separate.)
- `apply_evaluator_submission` → `apply_reducer_submission(submission: ReducerSubmission)`:
  body identical pattern — `_assert_submission_attempt`, `_mark_reducer`, advance.
- `_persist_plan_contract`: drop `evaluation_criteria=` kwarg (D3 keeps `plan_spec`); see
  attempt_store `set_plan_contract` edit.
- **`_persist_generator_tasks` + new `_persist_reducer_tasks`** — CRITICAL: build ONE
  `local_id → persisted_task_id` map across BOTH ordered lists FIRST (generators via
  `generator_task_id`, reducers via WS1 `reducer_task_id`), THEN upsert each task with `needs`
  resolved through that combined map. This replaces `dependency_task_ids` (which only knew the
  generator namespace). Shape:
  ```
  ordered_gen, ordered_red = ordered_plan_tasks(submission.tasks, submission.reducers)
  id_map = {t.local_id: generator_task_id(attempt.id, t.local_id) for t in ordered_gen}
  id_map |= {r.local_id: reducer_task_id(attempt.id, r.local_id) for r in ordered_red}
  # generators: upsert role GENERATOR, agent_name=t.agent_name, context_message=t.task_spec,
  #             needs=[id_map[d] for d in t.needs]
  # reducers:   upsert role REDUCER, agent_name=REDUCER_AGENT_NAME (WS1),
  #             context_message=r.prompt, needs=[id_map[d] for d in r.needs]
  ```
  Drop `task_center_attempt_id=` + `spawn_reason=` upsert kwargs (D5/WS6 removes those store
  params — coordinate; if WS6 hasn't landed, keep them to avoid a kwarg mismatch and flag).
- `_mark_evaluator` → `_mark_reducer`: assert stage RUN (not EVALUATE); the reducer is a normal
  DAG task — drop the `attempt.evaluator_task_id` match; instead resolve the task and
  `assert_reducer_task_for_submission(task, attempt)`; `_write_submission_status(role="Reducer",
  outcome=...)`. Reducer outcome is binary (success/failure) so the blocker→BLOCKED branch in
  `_write_submission_status` is unreachable for reducers (intended; §7 WS2).
- `_write_submission_status`: UNCHANGED logic (WS4 reworks the summary write).
- Imports: `assert_evaluator_task_for_submission` → `assert_reducer_task_for_submission`;
  `EvaluatorSubmission` → `ReducerSubmission`; `AttemptStage.GENERATE/EVALUATE` → `RUN`;
  `AttemptFailReason.{PLANNER,GENERATOR,EVALUATOR}_FAILED` → `TASK_FAILED`;
  `ordered_generator_tasks`+`dependency_task_ids` → `ordered_plan_tasks` (drop
  `dependency_task_ids`); `generator_task_id` + WS1 `reducer_task_id`.

DO NOT TOUCH (WS7): `apply_workflow_closure_report` (`:166-213`), `_build_handoff_rollup`
(`:215-240`), the `WorkflowClosureReport` import (`:43`), the `generator_summaries` imports
(`:19-24`). They collide with WS2 only in the import block — leave them; WS7 deletes them.

Risk: `apply_plan_submission` is the wiring junction; both tuples + stage RUN + reducer persist
must be atomic-enough that RUN never schedules a reducer with unresolved `needs`.

---

## File: `backend/src/task_center/_core/invariants.py`  — CORE (load-bearing, D5 coupling)

Current (verified):
- `assert_task_belongs_to_attempt` (`:122-126`): reads `task.get("task_center_attempt_id") !=
  attempt.id`. **WS6/D5 drops `task_center_attempt_id`** → this MUST switch to a task_id-prefix
  check.
- `assert_generator_task_for_submission` (`:129-132`): calls the above + role==GENERATOR.
- `assert_evaluator_task_for_submission` (`:135-138`): the above + role==EVALUATOR.
- imports `AttemptFailReason/AttemptStage/AttemptStatus` (`:14-19`), `Iteration`/`IterationStatus`
  (`:20`), `Workflow` (`:21`). `__all__` (`:141-157`).

Target:
- `assert_task_belongs_to_attempt`: replace the column read with a prefix check on `task["id"]`:
  the persisted task_id always starts with `{attempt.id}:` (`:planner` / `:gen:<lid>` /
  `:red:<lid>`). Use `str(task.get("id") or "").startswith(f"{attempt.id}:")`. This is the
  D5-forced rewrite — coordinate with WS6 but author it here (WS2 owns the reducer invariant and
  both submission-asserts route through this helper).
- `assert_evaluator_task_for_submission` → `assert_reducer_task_for_submission`: same body,
  role check `TaskCenterTaskRole.REDUCER.value`.
- `__all__`: `assert_evaluator_task_for_submission` → `assert_reducer_task_for_submission`.
- `Iteration`/`Workflow` imports unchanged by WS2 (WS6 may repoint to `_core.state`).

Risk: if WS6 hasn't dropped the column yet, the prefix check STILL works (the prefix invariant
holds today — verified: `planner_task_id`/`generator_task_id` both `{attempt_id}:`-prefixed).
So this edit is forward-safe and can land before WS6. Flag the D5 coordination but don't block.

---

## File: `backend/src/db/models/attempt.py`  — CORE (schema)

Current: `evaluation_criteria: Mapped[list[str]] JSON` (`:33`), `generator_task_ids: JSON`
(`:34`), `evaluator_task_id: Mapped[str|None] String(96)` (`:35`).

Target:
- DROP `evaluation_criteria` column; DROP `evaluator_task_id` column.
- KEEP `generator_task_ids` (unchanged).
- ADD `reducer_task_ids: Mapped[list[str]] = mapped_column(JSON, default=list)`.
- KEEP `plan_spec` (`:32`, D3 gate).

Risk: schema must match `db/engine.py` migration ops below.

---

## File: `backend/src/db/engine.py`  — CORE (migration, C2)

Current: `_DROPPED_COLUMNS` (`:40-69`) has `agent_runs`, `task_center_tasks`,
`task_center_runs` — **no `attempts` entry**. `_RENAMED_COLUMNS` (`:71-78`) has
`iterations`+`attempts` both `task_specification→plan_spec`.

Target (§7 WS2 C2):
- `_DROPPED_COLUMNS["attempts"] = {"evaluation_criteria", "evaluator_task_id"}`.
- `reducer_task_ids` is a **NEW column, NOT a rename** — do NOT add it to `_RENAMED_COLUMNS`.
  `create_all`/the SQLite rebuild path adds new ORM columns automatically (verified: the rebuild
  copies `col.name in existing` and create_all adds the rest). `generator_task_ids` stays out of
  `_RENAMED_COLUMNS` (unchanged).
- Leave the existing `attempts: task_specification→plan_spec` rename as-is (D3 keeps plan_spec).

Risk: durable app DBs are empty in dev; real rows live only in disposable
`task_center_runner/*.db` (per MEMORY: no enum-value migration needed; column add/drop IS
handled by these hooks). Verify a fresh run rebuilds the `attempts` table without the dropped
columns.

---

## File: `backend/src/db/stores/attempt_store.py`  — CORE

Current (verified): `insert` seeds `evaluation_criteria=[]`, `evaluator_task_id=None` (`:34,36`);
`set_plan_contract(... evaluation_criteria ...)` (`:64-81`); `set_generator_task_ids` (`:83-93`);
`set_evaluator_task_id` (`:95-105`); `_to_dto` maps `evaluation_criteria`/`generator_task_ids`/
`evaluator_task_id` (`:172-174`).

Target:
- `insert`: drop `evaluation_criteria=[]` + `evaluator_task_id=None`; add `reducer_task_ids=[]`.
- `set_plan_contract`: drop `evaluation_criteria` param + `record.evaluation_criteria = ...`
  (keep `plan_spec`, `deferred_goal`).
- `set_generator_task_ids`: unchanged.
- `set_evaluator_task_id` → `set_reducer_task_ids(self, attempt_id, task_ids: list[str])`:
  mirror `set_generator_task_ids` (sets `record.reducer_task_ids = list(task_ids)`).
- `_to_dto`: drop `evaluation_criteria=...` + `evaluator_task_id=...`; add
  `reducer_task_ids=tuple(record.reducer_task_ids or ())`. KEEP `plan_spec`.
- imports of `AttemptStage`/`AttemptFailReason`/`AttemptStatus` (`:10-15`) — WS6 may repoint to
  `_core.state`; values used (`PLAN`, `RUNNING`, `CLOSED`) unchanged by WS2 except `GENERATE`
  is never referenced here.

Risk: `set_reducer_task_ids` must be called by the orchestrator's `apply_plan_submission`.

---

## File: `backend/src/task_center/attempt/orchestrator_registry.py`  — CORE (protocol)

Current: `RegisteredAttemptOrchestrator` Protocol declares `apply_evaluator_submission`
(`:39`) + TYPE_CHECKING import of `EvaluatorSubmission` (`:18-22`) and `WorkflowClosureReport`
(`:17`).

Target: `apply_evaluator_submission` → `apply_reducer_submission(self, submission:
ReducerSubmission) -> None`; TYPE_CHECKING import `EvaluatorSubmission` → `ReducerSubmission`.
LEAVE `apply_workflow_closure_report` + `WorkflowClosureReport` import (WS7).

Risk: structural Protocol — must stay in sync with the orchestrator method rename or static
type-check fails.

---

## File: `backend/src/task_center/__init__.py`  — CORE (facade)

Current: TYPE_CHECKING + `_EXPORTS` reference `EvaluatorSubmission` (`:62,100`),
`ordered_generator_tasks` (`:32,127-130`), `PlannedGeneratorTask` (`:64,107`). Also exports
many WS6/WS7 symbols (`WorkflowOrigin*`, `attempt.deps`, `attempt.state`) — not WS2's to change.

Target (WS2 slice only):
- `EvaluatorSubmission` → `ReducerSubmission` (both TYPE_CHECKING `:62` and `_EXPORTS` `:100`,
  source `task_center.submissions`).
- `ordered_generator_tasks` → `ordered_plan_tasks` (TYPE_CHECKING `:32` + `_EXPORTS` `:127-130`,
  source `task_center.attempt.plan_dag` after the module rename).
- ADD `PlannedReducerTask` export (mirror `PlannedGeneratorTask`, source
  `task_center.submissions`) — `_schemas.py` and tests import it.
- Leave `PlannedGeneratorTask` (kept).

Risk: lazy `__getattr__` facade — a stale `_EXPORTS` path raises AttributeError at first access.
Module-path rename (`generator_dag`→`plan_dag`) must land WITH this edit.

---

## PROPAGATION FILES (mechanical: import repoint / vocab / string-match only)

These need NO new logic — only rename old symbols/strings to new ones once the core lands.

- `backend/src/tools/submission/evaluator/submit_evaluation_success/submit_evaluation_success.py`
  + `.../submit_evaluation_failure/submit_evaluation_failure.py` — WS1 MOVES these to
  `tools/submission/reducer/`. They import `EvaluatorSubmission` + call
  `apply_evaluator_submission`. If WS1 hasn't moved them, WS2's rename of those two symbols
  breaks these files → coordinate: WS1 owns the file move + `ReducerSubmission`/
  `apply_reducer_submission` call. Listed here so the partition is complete.
- `backend/src/tools/submission/verifier/submit_verification_*` — call
  `apply_generator_submission` (unaffected by WS2 directly); WS3 deletes them.
- `backend/src/tools/submission/context/executor.py` — calls `apply_generator_submission`
  (unchanged name). No WS2 edit unless it imports `AttemptDeps` (WS6).
- `backend/src/tools/subagent/run_subagent/prompt.py:79` — doc-string literal
  `apply_evaluator_submission` → `apply_reducer_submission` (pure string).
- `backend/src/task_center/agent_launch/composer.py` — VERIFIED NOT WS2: its `.deps`
  (`:62,65`) is `engine.deps` (the runtime deps object, WS6 territory), not
  `PlannedGeneratorTask.deps`. No WS2 edit. (False positive in the broad grep.)
- `backend/tests/unit_test/test_task_center/test_lifecycle/test_generator_dag.py` — asserts
  `ordered_generator_tasks`, `summarize_generator_dag`, `GeneratorDagSummary`,
  `ready_pending_generator_ids`. Rename to `ordered_plan_tasks`/`dag_status`/`DagStatus`/
  `ready_pending_plan_ids` AND update call sites to the two-arg signature + add reducer fixtures.
  (Borderline core — the reachability/≥1-reducer assertions are NEW tests; treat the NEW gate
  scenarios as core-test authoring, the renames as propagation.)
- `backend/tests/unit_test/test_task_center/test_domain/test_attempt_dto.py` — Attempt fields
  `evaluation_criteria`/`evaluator_task_id` → `reducer_task_ids`; stage `GENERATE`/`EVALUATE`.
- `backend/tests/unit_test/test_task_center/test_persistence/test_attempt_store.py` — store
  methods `set_evaluator_task_id`→`set_reducer_task_ids`, `set_plan_contract` arg, dropped fields.
- `backend/tests/unit_test/test_task_center/test_lifecycle/test_attempt_orchestrator.py` —
  `apply_evaluator_submission`→`apply_reducer_submission`, `EvaluatorSubmission`→
  `ReducerSubmission`, `AttemptStage.GENERATE/EVALUATE`, `AttemptFailReason.*` → `TASK_FAILED`.
- `backend/tests/unit_test/test_task_center/test_lifecycle/test_invariants.py` —
  `assert_evaluator_task_for_submission`→reducer; `task_center_attempt_id` prefix-check fixtures.
- `backend/tests/unit_test/test_task_center/test_lifecycle/test_concrete_class_annotations.py`,
  `test_orchestrator_composer.py`, `test_phase03_submission_integration.py`,
  `test_phase04_*.py`, `test_planner_capability_fork.py`,
  `test_iteration_attempt_coordinator.py`, `test_integration_phase02.py`,
  `test_integration_smoke.py` — vocab renames (EvaluatorSubmission, evaluator stage, fail
  reasons, ordered_generator_tasks). Each is a string/symbol substitution once core lands.
- `backend/tests/unit_test/test_tools/test_submission_planner_tools.py` +
  `submission_test_utils.py` — `evaluation_criteria` plan input → `reducers=[{id,prompt,needs}]`;
  `deps`→`needs`; new validation messages (no-reducer, unreachable). Heavy string churn (WS9).
- `backend/tests/unit_test/test_tools/test_submission_terminal_routing.py` — evaluator terminal
  vocab (coordinate WS1).
- `backend/src/task_center_runner/scenarios/pipeline/dependency_dag_mixed.py`,
  `dependency_dag_serial.py`, `planner_validation/duplicate_local_id.py` — inline plan dicts:
  add `reducers=[...]`, `deps`→`needs` (WS9 owns the bulk; listed for completeness).
- `backend/src/task_center/iteration/attempt_coordinator.py` — VERIFIED NOT WS2: contains NONE
  of `evaluation_criteria`/`evaluator_task_id`/`*_FAILED`/`.deps`/`AttemptStage.`/
  `ordered_generator`. False positive in the broad grep. No WS2 edit. (Its retry-context
  outcome reads are WS4/WS5, not WS2.)
- `backend/src/db/stores/task_center_store.py:205` `list_generator_tasks_for_attempt` +
  protocol `_core/persistence.py:191` — DEAD under WS2 (RUN sources by id) AND unbuildable
  under WS6 (the `task_center_attempt_id` column drop). REMOVE both (WS2's
  `_advance_generator_stage` was the ONLY consumer). VERIFIED.
- `backend/src/db/stores/task_center_store.py:191` `list_tasks_for_attempt` — **DO NOT remove
  in WS2**: VERIFIED it has another live consumer at
  `task_center_runner/core/runner.py:95`. It is still WS6/D5-blocked by the column drop, but
  that resolution belongs to WS6 (rework it to id-sourcing or keep a different filter), not WS2.

---

## OPEN DECISIONS (plan leaves unpinned — proposed choices)

1. **`PlannedReducerTask` DTO name/shape** (plan §2 lists fields, never names the dataclass).
   PROPOSE `PlannedReducerTask(local_id: str, needs: tuple[str,...], prompt: str)` in
   `submissions.py`, mirroring `PlannedGeneratorTask`. No `agent_name`.
2. **`ReducerInput.id` vs `local_id`** — PROPOSE `id` (match `PlanTaskInput.id`; the
   LLM-facing schema uses `id`, DTO uses `local_id`).
3. **`ordered_plan_tasks` signature** — PROPOSE two positional args
   `(generators, reducers)` returning `(ordered_generators, ordered_reducers)` tuple. The
   reachability rule needs the gen/reducer discriminator, so a single tagged list is worse.
4. **`ReducerSubmission` status field name** — PROPOSE keep `outcome:
   Literal["success","failure"]` (WS4 unifies `outcome→status` across Generator+Reducer in one
   pass). If WS4 lands first, use `status`.
5. **RUN task-record sourcing** — PROPOSE loop `get_task` over the union of both tuples (no new
   batch method). Alternative: add `list_tasks_by_ids` to store + protocol. Loop is minimal.
6. **`ready_pending_generator_ids` new name** — PROPOSE `ready_pending_plan_ids`.
7. **`AttemptStageAdvancer` class name** — PROPOSE KEEP (renaming the class is cosmetic churn
   across `orchestrator.py` construction + tests; module is renamed to `run_stage.py` which is
   the §4 mandate). Flag for the renamer (WS10) if a class rename is wanted.
8. **`plan_spec` retention** — D3/M2-GATED. WS2 KEEPS `plan_spec` on PlannerSubmission / Attempt
   / `set_plan_contract` / model / DTO. Only `evaluation_criteria` is removed. If the WS9
   confirmation says prompts don't need a global narrative, a LATER pass removes `plan_spec`.
   WS2 must NOT remove `plan_spec`.
9. **`apply_planner_failure` fail reason** — PROPOSE `AttemptFailReason.TASK_FAILED` (planner
   failure is a task failure; the role tells you it was the planner). `_mark_startup_failed`
   keeps `STARTUP_FAILED`.
10. **`dependency_task_ids` fate** — PROPOSE DELETE (the combined local→id map is built inline in
    the orchestrator persist path, since only there are both tuples in scope). If WS keeps a
    helper, it must take the full map, not assume the generator namespace.

---

## DRIFT (plan line-claims vs current code)

- Plan WS2 cites `_schemas.py:61` for `evaluation_criteria` and `:31` for `deps` and `:70-75`
  for the validator. VERIFIED present but: `evaluation_criteria` field is `:61`, its validator
  `_validate_evaluation_criteria` is `:70-75` (matches); `PlanTaskInput.deps` is `:31` (matches).
  Accurate.
- Plan WS2 cites `stage_advancer.py:101-119,203-265` for evaluator stage methods. VERIFIED:
  `_advance_evaluator_stage` `:101-119`, `_start_evaluator_stage` `:203-265`. Accurate.
- Plan WS2 cites `orchestrator.py:161-164,307-325` for apply/mark evaluator. VERIFIED:
  `apply_evaluator_submission` `:161-164`, `_mark_evaluator` `:307-325`. Accurate.
- Plan WS2 cites `_core/invariants.py:135-138`. VERIFIED `assert_evaluator_task_for_submission`
  `:135-138`. Accurate.
- **DRIFT (material):** Plan WS2 ("Persistence — two tuples") says "DB: `_DROPPED_COLUMNS` +=
  `attempts:{evaluation_criteria, evaluator_task_id}`". VERIFIED `_DROPPED_COLUMNS` has NO
  `attempts` key today (`:40-69`) — the implementer ADDS the whole key, not "+=" to an existing
  one.
- **DRIFT (material, NOT in plan WS2):** Plan WS2 never states that
  `list_generator_tasks_for_attempt` / `list_tasks_for_attempt` filter on
  `task_center_attempt_id` (dropped by WS6/D5), so "reuse `ready_pending_*`" silently requires a
  new id-based sourcing. Captured in the CRITICAL section.
- **DRIFT (material, NOT in plan WS2):** Plan WS2 never lists `_core/invariants.py:122-126`
  `assert_task_belongs_to_attempt`'s `task_center_attempt_id` read as a WS2 touch, but WS2's
  reducer/generator submission-asserts route through it and WS6 drops the column → WS2 must
  rewrite it to a prefix check. Captured in the invariants section.
- Plan §3 names the module `plan_dag.py` and fn `ordered_plan_tasks`; current file is
  `generator_dag.py` / `ordered_generator_tasks`. Matches the rename mandate (not drift, noted).
- Plan §2 `AttemptStage : PLAN | RUN | CLOSED` — current has 4 incl. `GENERATE`+`EVALUATE`;
  RUN replaces GENERATE (value `"generate"`→`"run"`). The plan calls it "rename `GENERATE`→`RUN`"
  in §4; explicit.
