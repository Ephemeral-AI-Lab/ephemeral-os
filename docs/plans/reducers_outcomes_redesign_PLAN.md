# Reducers + Unified Outcomes Redesign — Implementation Plan (Reconciled)

**Status:** RECONCILED and execution-ready. Folds 15 directives (D1–D15) + two review
rounds (R1 simplification, R2 final review incl. its confirmation pass) into one
internally-consistent spec. The round-by-round directive log + the review reports live in
this file's git history; this document is the settled result.

**Vocabulary note (no "node").** There is no separate "node" concept. Everything that runs
is a **task** (`TaskCenterTask`), distinguished by **role** (planner | generator | reducer
| helper | subagent). The **plan** is the DAG the planner authors — its **generator** and
**reducer** tasks, edges = `needs`. The planner is the task that *authors* the plan; it is
not *in* the plan. So "the plan's tasks" always means the generator + reducer tasks.

```
task   — any agent run (TaskCenterTask), by role
plan   — the planner-authored DAG of generator + reducer tasks (edges = needs)
gate   — all the plan's tasks reach DONE
```

---

## 1. Target model (end state)

**Roles:** `planner`, `generator` (only profile `executor`), `reducer`, `helper`
(advisor), `subagent` (explorer). **Gone:** the `evaluator` role and the `verifier`
profile.

**An attempt is a planner-authored plan: a DAG of generator + reducer tasks; edges are
`needs`.**
- **generator task** `{local_id, agent_name, needs, task_spec}` — does work (role
  `GENERATOR`); terminals `submit_execution_success` / `submit_execution_blocker` /
  `submit_workflow_handoff`.
- **reducer task** `{local_id, needs, prompt}` — digests/gates (role `REDUCER`); the exit
  gate; `prompt` required + nonblank; terminals `submit_reduction_success` /
  `submit_reduction_failure` (binary).

**Gate + stage collapse.** Stages collapse to `PLAN → RUN → CLOSED`. The single RUN stage
schedules the plan's tasks to quiescence via the existing ready/`dag_status` machinery. The
attempt PASSES iff every plan task reaches DONE; FAILS if any plan task failed/blocked
(`TASK_FAILED`). Two structural rules, enforced in `ordered_plan_tasks`, keep "every
attempt has an exit AND all work is judged" by construction: **≥1 reducer**, and
**reachability** — every generator transitively needed by ≥1 reducer (a generator no
reducer needs would reach DONE unjudged).

**Outcomes algebra (recursive; replaces every "summary").**
- `Outcome = {local_id, status, text, children: tuple[Outcome], failure, raw_status}`
  (`_core/outcomes.py`).
- `generator.outcomes` / `reducer.outcomes` = the agent's terminal result (singleton list
  normally).
- `attempt.outcomes` = ⋃ its **reducers'** outcomes.
- `iteration.outcomes` (**persisted, canonical**) = the **passing** attempt's reducer
  outcomes; **failure-aware** — a failed iteration carries its last failed attempt's
  failed-task outcomes + `fail_reason`.
- `workflow.outcomes` (**derived, not stored**) = the **last iteration's** outcomes.

**No closure abstraction.** Outcomes + status propagate up; there is no `*ClosureReport`,
no closure router, no `final_outcome`. "close" remains as a *state transition* (close
attempt/iteration/workflow → set status); the *report/router layer* is gone.

**Child-workflow handoff (the one cross-level action).** A generator can
`submit_workflow_handoff` a child workflow. Bidirectional 1:1 link, set in one
transaction: `Task.child_workflow_id` (forward) ↔ `Workflow.parent_task_id` (backward).
The forward link derives the generator's outcomes (`child_workflow_id → workflow.outcomes`);
the backward link drives the close-time resolution and the recursion-depth walk-up (both
O(1), no store scan).

**The root run.** Every workflow is generator-spawned, **including the root**, via a
**synthetic run-level bootstrap generator task** the run controller owns (not a real
agent — §6). The run finishes when that bootstrap task resolves DONE. The run's result =
the bootstrap task's outcomes = the root `workflow.outcomes`.

**Off-spine roles (excluded from the algebra):** `planner` is control-plane (its
`submit_plan_*` configures the attempt); `advisor`/`explorer` are callee-returns. Only
generator + reducer tasks (+ child workflows) are on the outcomes spine.

**Retry / relay.** A failed attempt re-plans from scratch (attempts immutable); the failed
attempt's failed-task outcomes + `fail_reason` cross to the retry planner. The next
iteration's planner reads prior iterations' reducer outcomes (`iteration.outcomes`).

---

## 2. Class / field reference

```
ROLES   (_core/task_state.py)
  TaskCenterTaskRole   : PLANNER | GENERATOR | REDUCER        (SpawnReason enum REMOVED)
  TaskCenterTaskStatus : PENDING | RUNNING | WAITING_WORKFLOW | DONE | FAILED | BLOCKED

DOMAIN DTOs   (all consolidated into _core/state.py — D11)
  WorkflowStatus       : OPEN | SUCCEEDED | FAILED | CANCELLED
  Workflow   : workflow_id · task_center_run_id · workflow_goal · status · iteration_ids
               parent_task_id: str|None            (spawning generator task; backward link)
               created/updated/closed_at · is_open
  IterationStatus          : OPEN | SUCCEEDED | FAILED | CANCELLED
  IterationCreationReason  : INITIAL | DEFERRED_GOAL_CONTINUATION
  Iteration  : iteration_id · workflow_id · sequence_no · creation_reason · iteration_goal
               attempt_budget · status · attempt_ids · deferred_goal_for_next_iteration
               outcomes (json list[Outcome]) · created/updated/closed_at
  AttemptStage      : PLAN | RUN | CLOSED
  AttemptStatus     : RUNNING | PASSED | FAILED
  AttemptFailReason : TASK_FAILED | STARTUP_FAILED        (a task failed — its role says which; or startup)
  Attempt    : attempt_id · iteration_id · attempt_sequence_no · stage · status
               planner_task_id: str|None
               generator_task_ids: tuple[str] · reducer_task_ids: tuple[str]   (TWO tuples — C2)
               deferred_goal_for_next_iteration · fail_reason · created/updated/closed_at

OUTCOMES   (_core/outcomes.py)
  Outcome : { local_id, status, text, children: tuple[Outcome,...], failure: str|None, raw_status: str|None }
            handoff generator emits ONE Outcome whose `children` = the child workflow's outcome list (MN2)

AGENT / Task   (db: task_center_tasks) — everything that runs is one of these
  Task : task_id · task_center_run_id · role · agent_name · context_message · status
         needs: list[str] · outcomes (json list[Outcome]) · terminal_tool_result (json)
         agent_run_id: str|None · child_workflow_id: str|None · created/updated_at
         (REMOVED: summaries→outcomes+terminal_tool_result · fix_target_id · context_packet_id
          · task_center_attempt_id (task_id encodes the attempt) · spawn_reason)

SUBMISSIONS   (task_center/submissions.py)
  PlannerSubmission   : attempt_id · planner_task_id · kind · tasks[] · reducers[]
                        deferred_goal_for_next_iteration · text       (NO plan_spec, NO evaluation_criteria)
  GeneratorSubmission : attempt_id · task_id · status · outcomes[] · terminal_tool_result
  ReducerSubmission   : attempt_id · task_id · status:Literal["success","failure"] · outcomes[] · terminal_tool_result

PLAN tasks (planner submit input)
  generator : { local_id, agent_name, needs, task_spec }
  reducer   : { local_id,             needs, prompt }      (prompt nonblank; ≥1; reachability)
```

**Deleted classes (net):** `WorkflowOriginKind` · `WorkflowOrigin` · `WorkflowClosureReport`
· `WorkflowClosureDeliveryResult` · `IterationClosureReport` · `ClosureOutcome` ·
`TerminalSuccess`/`SuccessDeferred`/`AttemptPlanFailed` · `SpawnReason` ·
`AttemptDelegatedWorkflowParentTask`.

---

## 3. File / folder structure (end state)

```
task_center/
├── _core/
│   ├── state.py              + Workflow · Iteration · Attempt + 6 lifecycle enums   (consolidates 3× state.py)
│   ├── outcomes.py           ← generator_summaries.py   (Outcome + algebra)
│   ├── task_state.py           TaskCenterTaskRole · TaskCenterTaskStatus
│   ├── terminal_routing.py   ← terminal_tool_routing.py (+ nested_workflow_depth, from ancestry.py)
│   ├── primitives.py           task-id helpers (planner_/generator_/reducer_task_id) + lifecycle config
│   ├── persistence.py · invariants.py · audit.py
├── run_controller.py         + the root run path (seed bootstrap generator, root close → finish_run; C1)
├── workflow/
│   ├── lifecycle.py · starter.py · __init__.py        (✗ state.py ✗ ancestry.py ✗ closure_report_router.py)
├── iteration/                  (KEPT — parallel to workflow/ + attempt/; owns the attempt retry loop)
│   ├── attempt_coordinator.py · __init__.py            (✗ state.py)
├── attempt/
│   ├── orchestrator.py        (+ start/apply/cancel_child_workflow; + AttemptDelegatedWorkflowParentTask dissolved in)
│   ├── orchestrator_registry.py · launch.py (+ AgentLaunch + AttemptDeps) · plan_dag.py · __init__.py
│   ├── run_stage.py           ← stage_advancer.py       (✗ deps.py ✗ state.py)
├── context_engine/
│   ├── engine.py             ← core.py
│   ├── scope.py · packet.py · renderer.py · tag_dictionary.py · recipes_registry.py
│   ├── agent_directives.py · task_guidance.py · context_outline.py · exceptions.py
│   └── recipes/
│       ├── planner.py        (+ folded iterations.py + attempts.py block builders; R1a)
│       ├── generator.py · reducer.py (← evaluator.py) · _needs.py · _task_xml.py · __init__.py
├── submissions.py · agent_launch/ · entry/bootstrap.py

tools/submission/
├── reducer/ (← evaluator/) · executor/ (submit_workflow_handoff ← submit_execution_handoff) · ✗ verifier/
├── planner/ · advisor/ · explorer/ · context/ · notification_triggers/ · _factory.py

agents/profile/main/   reducer.md (← evaluator.md) · executor.md · planner.md · ✗ generator_verifier.md

db/models/  workflow.py(+parent_task_id; ✗final_outcome/origin_kind/requested_by_task_id) ·
            iteration.py(task_summary→outcomes; ✗plan_spec) ·
            attempt.py(generator_task_ids+reducer_task_ids; ✗evaluation_criteria/evaluator_task_id/plan_spec) ·
            task_center.py(Task per §2)
```

`plan_dag.py` (was `generator_dag.py`) is the plan's task-DAG validator/scheduler — named
for the *plan*, since not every task is in it (planner/advisor/explorer aren't). The
existing `audit/node_id.py` is an unrelated audit-tree concept and is untouched. New file
`run_controller.py` owns the C1 root path (§6), distinct from `entry/bootstrap.py`
(process/sandbox wiring). Optional staged folds (your call, post-green): R1d
`run_stage.py`→`orchestrator.py`; R1e `_core/task_state.py`→`_core/state.py`.

---

## 4. Unified vocabulary

Mandatory renames, each retiring a real inconsistency:

| Concept | Was | Now |
|---|---|---|
| gate/digest role | `evaluator` | `reducer` |
| DAG edge | `deps`/`dependency` (+ `needs` in DB) | **`needs`** everywhere (wrapper tag `<dependency>`→`<needs>`; child element stays `<task>`) |
| result unit / field | `TaskOutcome.summary` | `Outcome.text` |
| result collection | `summary`/`task_summary`/`summaries`/`generator_summaries` | **`outcomes`** (+ raw terminal → `terminal_tool_result`) |
| submission status | `outcome` | `status` |
| DAG quiescence | `GeneratorDagSummary`/`summarize_generator_dag` | `DagStatus`/`dag_status` (D15) |
| attempt stages | `PLAN/GENERATE/EVALUATE/CLOSED` | `PLAN/RUN/CLOSED` |
| fail reason | `GENERATOR_FAILED`+`EVALUATOR_FAILED`+`PLANNER_FAILED` | **`TASK_FAILED`** (role of the failed task says which) |
| plan task tuples | `generator_task_ids`+`evaluator_task_id` | `generator_task_ids`+`reducer_task_ids` (C2) |
| goal fields | `goal` | `workflow_goal` / `iteration_goal` |
| modules | `generator_summaries.py`/`generator_dag.py`/`stage_advancer.py`/`core.py`/`terminal_tool_routing.py` | `outcomes.py`/`plan_dag.py`/`run_stage.py`/`engine.py`/`terminal_routing.py` |
| DAG validator fn | `ordered_generator_tasks` | `ordered_plan_tasks` |
| handoff terminal | `submit_execution_handoff` | `submit_workflow_handoff` |
| parent link | `requested_by_task_id` (workflow→task) | `parent_task_id` (+ `child_workflow_id` fwd) |

**Deferred (Tier-2, explicit follow-up):** child element `<task>`→`<outcome>` (then
`_task_xml.py`→`_outcome_xml.py`); `PlannerSubmission.tasks`→`generators`. Deferred to keep
the mock string-match churn bounded.

---

## 5. The three context recipes

Generator and reducer share one helper; the evaluator's bespoke assembly is deleted.

```
planner  : <workflow_goal> + <iteration_goal> + prior-attempt outcomes (if any)
             ├ relay : prior iterations' canonical reducer outcomes (iteration.outcomes)        [feed-forward]
             └ retry : current iteration's failed attempts → failed-task outcomes + <failure>    [feedback]
generator: <needs>(outcomes) + <assigned_task>          (plan_spec removed; symmetric with reducer)
reducer  : <needs>(outcomes) + <assigned_prompt>
shared   : recipes/_needs.py (needs_outcome_blocks, both roles; <needs> wrapper, <task> child)
deleted  : recipes/evaluator.py, current_attempt_flat_blocks, <evaluation_criteria>/<evaluator_summary>
folded   : recipes/iterations.py + recipes/attempts.py → planner.py (R1a; planner is their only consumer)
```

The planner recipe's two history paths are **two data sources, one rendering**: relay is
reducer-only (the iteration passed, reducers ran); retry is failed-task (any role) +
`fail_reason` (when a generator fails its reducer never runs, so reducer-only would be
empty).

---

## 6. The root run path (C1 + COND-1 — authored)

The deleted entry-origin closure path (`_deliver_entry_origin → finish_run`) needs a
replacement that fits the generator-spawned model. **The root is a synthetic control task,
stated plainly** — it does not loop through the engine, has no profile/sandbox/`agent_run_id`,
and is driven by `run_controller.py`. It exists to give the root workflow a uniform
`parent_task_id` and to carry the run's result as `outcomes`.

**Seed (replaces `WorkflowStarter.start(origin=entry)` at `bootstrap.py:132`):**
1. `_create_top_level_run()` creates the request + run + sandbox binding (unchanged,
   `bootstrap.py:150-168`).
2. `run_controller` creates a **run-level bootstrap generator task**: `role=GENERATOR`,
   `task_id="<run_id>:root"` (no attempt prefix — how the root is recognized), seeded
   **`status=RUNNING`** (NOT `WAITING_WORKFLOW`; the link doesn't exist yet — COND-1).
3. `WorkflowStarter.start(prompt, parent_task_id="<run_id>:root")` creates the root
   workflow **and atomically** flips the bootstrap task `RUNNING → WAITING_WORKFLOW` while
   setting `child_workflow_id` (one transaction — mirrors `starter.py:206-211`'s
   `_mark_parent_waiting`; this also relaxes the `starter.py:171` RUNNING / `:143`
   attempt-bound guards for the run-level case). The whole seed+start runs under a
   **failsafe**: any throw → `run_controller` calls `finish_run(run_id, "failed")` (the
   equivalent of today's `bootstrap.py:137` `_finish_run_if_open`), so a seed/start failure
   can never leave the run OPEN or the bootstrap task stranded `WAITING_WORKFLOW`.

**Resolve (replaces `_deliver_entry_origin`):** the workflow-close handler inspects the
closing workflow's `parent_task_id`:
- parent task **has an attempt prefix** → normal handoff: route to that attempt's
  orchestrator `.apply_child_workflow_outcome(...)` (D14).
- parent task **has no attempt prefix** (`"<run_id>:root"`) → **root**: `run_controller`
  writes the bootstrap task's `outcomes = workflow.outcomes`, marks it DONE/FAILED, and
  calls `finish_run(run_id, status=done/failed)`.

**Why synthetic, not a real agent (the explicit trade):** a real looping root generator
would pull in the entire agent-launch surface (profile, sandbox, context packet,
`agent_run_id`, a terminal call) for a task that does no user work — its only job is to
delegate the root workflow and hold the result. The synthetic task buys the uniform
`outcomes`/`parent_task_id` plumbing without that cost. It is the old entry-origin control
path, re-expressed in the outcomes vocabulary.

**Verify (§10):** happy run → `finish_run` with run `outcomes` = root `workflow.outcomes`;
failed-root-**workflow** → run `failed` with the failed last iteration's outcomes +
`fail_reason`; failed-root-**seed** (a throw during seed/start) → run `failed`, bootstrap
task not stranded `WAITING_WORKFLOW`.

---

## 7. Workstreams

Each: the change, principal seams (file:line), verification. The seam inventory is
load-bearing — it is the part that did not survive in §1–§6 and must be executed.

### WS1 — Reducer role replaces evaluator
- **Roles:** `AgentRole.EVALUATOR`→`REDUCER` (`agents/definition/model.py:38`);
  `TaskCenterTaskRole.EVALUATOR`→`REDUCER` (`_core/task_state.py:16`); loader validation
  (`agents/definition/loader.py:65-69`). `SpawnReason` enum + `task.spawn_reason` **removed**
  (D5).
- **Profile/terminals:** `agents/profile/main/evaluator.md`→`reducer.md`;
  `tools/submission/evaluator/*`→`reducer/*` (`submit_reduction_success/failure`); registry
  descriptors (`tools/_terminals/registry.py:112-140`); `_factory.py` wiring.
- **Recipe/scope:** `recipes/evaluator.py`→`reducer.py` (§5); `ContextScope.for_evaluator`
  →`for_reducer` (now takes `task_id`) (`scope.py:88-101`).
- **Launch/ids:** `EVALUATOR_AGENT_NAME`→`REDUCER_AGENT_NAME`; `for_evaluator`→`for_reducer`
  (`attempt/launch.py:303,354-369`); `_ROLE_FAIL_REASONS`→`TASK_FAILED` (`:197-200`);
  `primitives.py` drop `evaluator_task_id`, add `reducer_task_id(attempt_id, local_id)`
  (`:red:<local_id>`, mirroring `generator_task_id`).
- **Verify:** reducer recipe + role-resolution unit tests; ruff + type-check green.

### WS2 — Gate, two tuples, stage collapse
- **Schema (`tools/submission/planner/_schemas.py`):** replace
  `evaluation_criteria: Field(min_length=1)` (`:61`) with `reducers: list[ReducerInput]
  (min 1)`, `ReducerInput={id, needs, prompt}` (`prompt` required + nonblank, reuse the
  `:70-75` validator — D6); rename `PlanTaskInput.deps`→`needs` (`:31`);
  `ordered_generator_tasks`→`ordered_plan_tasks` validating the combined DAG: unique ids,
  known needs, no cycles, **≥1 reducer**, **reachability** (every generator transitively
  needed by ≥1 reducer).
- **DTOs:** `submissions.py` `evaluation_criteria`→`reducers`; `PlannedGeneratorTask.deps`
  →`needs`; `EvaluatorSubmission`→`ReducerSubmission` (binary status — keeps the shared
  `_write_submission_status` blocker→BLOCKED branch unreachable for reducers).
- **Persistence (two tuples — C2):** `Attempt` gets `generator_task_ids` +
  `reducer_task_ids`; **drop `evaluator_task_id`** (in `_core/state.py` now). DB
  (`db/engine.py`): `_DROPPED_COLUMNS` += `attempts:{evaluation_criteria, evaluator_task_id}`;
  **ADD a new `reducer_task_ids` column** (a new column — **not** a rename; `generator_task_ids`
  is unchanged and stays out of `_RENAMED_COLUMNS`).
- **Stage machine:** delete `AttemptStage.EVALUATE`, `_start_evaluator_stage`,
  `_advance_evaluator_stage` (`stage_advancer.py`→`run_stage.py:101-119,203-265`);
  `_advance_generator_stage`→`_advance_run_stage` over the union of both tuples; reuse
  `ready_pending_*` + `dag_status` (D15). Close: all plan tasks DONE→PASSED; any
  failed/blocked→FAILED(`TASK_FAILED`).
- **Orchestrator:** `apply_evaluator_submission`→`apply_reducer_submission`
  (`orchestrator.py:161-164,307-325`); invariant `assert_evaluator_task_for_submission`
  →reducer (`_core/invariants.py:135-138`); reducer submission marks its task through the
  *same* path as a generator.
- **Verify:** pipeline scenarios (pass/fail/retry, diamond/parallel/serial) green under
  reducers; **add** §10's gate scenarios.

### WS3 — Remove the verifier profile
- Delete `agents/profile/main/generator_verifier.md` + `tools/submission/verifier/*`
  (`submit_verification_*`); `_factory.py:16-35`; registry `:141-169`. Drop `"verifier"`
  from `_REQUIRED_AGENT_NAMES` (`task_center_runner/core/bootstrap.py:28`), audit role sets
  (`audit/recorder.py:87,94`, `audit/node_id.py:15`), the directive
  (`agent_directives.py:20`), task-guidance dispatch (`agent_launch/task_guidance_dispatch.py:26-36`).
- Rework verifier-spawning scenarios to `executor` generators + `reducer` gates
  (`full_case_user_input.py`, `full_stack_adversarial.py`, `pipeline/nested_workflow.py`,
  `pipeline/deferred_parent_planner_terminal_routing.py` + their hooks + asserting tests).
- **Verify:** full mock suite green; `grep` finds no `verifier`/`verification`.

### WS4 — Unified `outcomes`; retire `summary`
- **Type:** `_core/generator_summaries.py`→`_core/outcomes.py`; `TaskOutcome`→`Outcome`
  (`summary`→`text`; `raw_status` kept); `GeneratorDagSummary`→`DagStatus` /
  `summarize_generator_dag`→`dag_status` (D15, now in `plan_dag.py`). `from_record` reads
  legacy `"summary"` for pre-migration rows.
- **Task storage (D5):** `Task.summaries`→`outcomes` (list[Outcome]) + `terminal_tool_result`
  (the raw terminal payload). The submit path (`orchestrator.py:~348`
  `_write_submission_status`) writes `outcomes` + `terminal_tool_result`; `latest_task_summary`
  is **removed** (readers project `Outcome`s directly).
- **Aggregation:** `_achieved_record_for`→`_iteration_outcomes_for` projects **reducer**
  outcomes (`iteration/attempt_coordinator.py:215-223`); `iteration.outcomes` is
  failure-aware; `workflow.outcomes` derived from the last iteration.
- **Persistence:** `Iteration.task_summary`→`outcomes` (`_RENAMED_COLUMNS` +=
  `iterations:{task_summary→outcomes}`); the run report surfaces `workflow.status` + derived
  `workflow.outcomes` (`task_center_runner/core/runner.py:130-133`).
- **MN2:** a handoff generator emits ONE `Outcome` whose `children` = the child workflow's
  outcome list; `to_record` nests accordingly.
- **Verify:** `Outcome` round-trip (incl. legacy `"summary"`); relay shows reducer outcomes;
  run-report shows `workflow.outcomes`.

### WS5 — Relay + retry (two projections, in `planner.py`)
- After R1a the planner block builders live in `planner.py`. Relay renders prior iterations'
  reducer outcomes from `iteration.outcomes`; retry renders each failed attempt's
  failed-task outcomes + `fail_reason` (generalizes `attempt_failure_line`).
  `<workflow_goal>`/`<iteration_goal>` + the deferred-goal handoff unchanged.
- **Verify:** deferral scenario shows iter N+1 planner = iter N reducer outcomes; retry
  scenarios cover both failed-reducer and **failed-generator-before-reducer** cases.

### WS6 — State consolidation + module dissolutions
- **`_core/state.py`** absorbs `Workflow`/`Iteration`/`Attempt` + their enums; delete
  `workflow/state.py`, `iteration/state.py`, `attempt/state.py`; repoint importers
  (`outcomes.py`, `invariants.py`, `persistence.py`, the three coordinators) to `_core.state`.
  Leaf modules → no cycle.
- **`attempt/deps.py` removed (D13):** `AgentLaunch` + `AttemptDeps` → `attempt/launch.py`
  (cycle-free: launch.py has no edge to orchestrator.py); `AttemptDelegatedWorkflowParentTask`
  → dissolved into orchestrator handoff methods (WS7). Update importers (`__init__.py:34`,
  `entry/bootstrap.py:30`, `starter.py:30`, `stage_advancer.py:22`, `orchestrator.py:36`,
  `tools/submission/context/executor.py:17`).
- **`workflow/ancestry.py` removed (D10):** `nested_workflow_depth` → private helper in
  `_core/terminal_routing.py` (its sole caller, the `is_nested` predicate); walks up via
  `Workflow.parent_task_id` (direct) and parses the parent attempt from the parent task's
  `task_id` prefix (`task_center_attempt_id` is gone). Update `__init__.py:21` chain note.
- **Store signatures (MN3):** D5 removes `task_center_attempt_id` + `summaries`; update
  `upsert_task` (`db/stores/task_center_store.py:126`) and `set_status`
  (`_core/persistence.py:59`, drops `final_outcome`) **before** the run controller (§6) and
  the submit path call them, or they pass now-removed kwargs.
- **`iteration/` package KEPT** (owns the attempt retry loop); only `state.py` leaves; the
  lazy `__init__.py` facade may simplify to a plain re-export.
- **Verify:** ruff/type-check green; no imports of `attempt.state`/`iteration.state`/
  `workflow.state`/`attempt.deps`/`workflow.ancestry`.

### WS7 — Closure removal + child-workflow handoff + orphan-guard
- **Remove** `WorkflowClosureReport` + `to_final_outcome` + `WorkflowClosureDeliveryResult`,
  `IterationClosureReport`+`ClosureOutcome`, `Workflow.final_outcome`, `WorkflowOriginKind`/
  `WorkflowOrigin`/`origin_kind`/`requested_by_task_id`, `closure_report_router.py`,
  `_build_handoff_rollup`/`child_outcomes_for_workflow`/`_handoff_rollup`,
  `apply_workflow_closure_report`.
  - DB: `_DROPPED_COLUMNS` += `workflows:{final_outcome, origin_kind, requested_by_task_id}`;
    + `workflows.parent_task_id` (new).
  - Consumers of `final_outcome` repointed: `runner.py:130-133`, `audit/recorder.py:111`,
    `db/stores/workflow_store.py:44,77,85,127`, `_core/persistence.py:59` (the `set_status`
    signature drops `final_outcome`); `final_attempt_id` survives as a delivery param.
- **Handoff lifecycle (D14) — three orchestrator methods, no wrapper class, no "wake":**
  - `start_child_workflow(generator_task, child_workflow)` (was `mark_waiting_workflow`):
    set `WAITING_WORKFLOW` + the bidirectional link.
  - `apply_child_workflow_outcome(generator_task, child_workflow)` (was
    `apply_workflow_closure_report`): on close, write generator `outcomes =
    child workflow.outcomes`, mark DONE/FAILED, advance the DAG.
  - `cancel_child_workflow(generator_task)` (was `restore_running_after_failed_workflow_start`).
  - Close routing: `parent_task_id` → parent task → attempt → `orchestrator_registry.get(attempt_id)`
    (or the root handler, §6).
- **Orphan-guard (M1) — DECISION, not "verify later":** keep a **state-level last resort**.
  If `start_child_workflow`/`cancel_child_workflow` fails, force the generator
  `WAITING_WORKFLOW → FAILED` via `set_task_status_if_current` with empty/failed outcomes,
  so it can never be stranded. (Replaces the deleted `starter.py:265-279` synthetic-failed-
  report compensation; same invariant, simpler. The root's equivalent lives in §6's seed
  failsafe.)
- **Verify:** recursion/handoff scenarios show parent `outcomes.children` = child
  `workflow.outcomes`; **failed-child** handoff carries failed outcomes + `fail_reason`;
  **deferred-multi-iteration child** still resolves (last-iteration outcomes); the M1
  scenario: handoff-start fails AND cancel fails → generator ends FAILED, never stuck.

### WS8 — Root run path (C1 / COND-1)
Implement §6: `run_controller.py` (seed synthetic bootstrap generator `RUNNING`; root close
handler → `finish_run`; the seed/start failsafe → `finish_run("failed")`); `WorkflowStarter.start`
takes `parent_task_id` instead of `WorkflowOrigin` and **atomically** flips the parent task
`RUNNING→WAITING_WORKFLOW` + sets the link (relaxing the `starter.py:143,171` guards for the
run-level case); `entry/bootstrap.py:120-148` calls the run controller instead of the
entry-origin start. **Verify:** §6's three cases (happy / failed-root-workflow /
failed-root-seed).

### WS9 — Mock harness, tests, migration, docs
- **Plan-submitting scenarios (~31 inline plan dicts + the 3 `plan_shapes.py` helpers):**
  every one gains `reducers=[{id, prompt, needs:[…]}]` (the new `min 1`), not just verifier
  ones.
- **`evaluation_criteria` response-builders** (`full_case_user_input.py:117`,
  `nested_workflow.py:118,161`, `full_stack_adversarial.py:155`,
  `attempt_budget_exhausted.py:71`, `dependency_dag_diamond.py:51`,
  `dependency_blocked_descendants.py:56`) become reducer-response builders.
- **Mock vocab string-matches:** `scenario_loop_runner.py:303` (`<evaluation_criteria>`→
  `<assigned_prompt>`/`<needs>`), `:274,302` (`<task `/`<assigned_task`),
  `test_initial_messages_capture.py:235-251`.
- **Context-engine unit tests** asserting the `<dependency>`→`<needs>` wrapper:
  `test_role_context_matches_diagram.py:289`, `test_recipes_other.py:200`,
  `test_renderer.py:155-175`, `test_task_guidance.py:90,156`, `test_tag_dictionary.py:46`,
  `test_context_outline.py:165,177`.
- **Audit:** role enums/sets; drop `evaluator_task_id`; `summaries`→`outcomes` projections
  (`audit/recorder.py`, `events.py`, `node_id.py`).
- **Docs/CLAUDE.md:** update `docs/architecture/task_center/*` + CLAUDE.md (state in
  `_core/state.py`; no closure router; the handoff/root model; no "node" concept).
- **Verify:** full mock suite green; the §10 scenarios pass.

### WS10 — Naming + import flattening (R1)
Module renames per §4 (`core.py`→`engine.py`, `stage_advancer.py`→`run_stage.py`,
`terminal_tool_routing.py`→`terminal_routing.py`, `generator_dag.py`→`plan_dag.py`,
`generator_summaries.py`→`outcomes.py`); R1a fold (iterations+attempts→planner.py); the
`evaluator.py`→`reducer.py` + `submit_execution_handoff`→`submit_workflow_handoff` renames
land with their workstreams. **Verify:** grep finds none of the old module/symbol names.

---

## 8. Decisions (resolved)

- **D1** reducer terminals = `submit_reduction_success/failure` pair.
- **D2** `goal`→`workflow_goal`/`iteration_goal`.
- **D3** `plan_spec` removed entirely — **M2:** gated on the WS9 confirmation that
  planner/executor prompts don't rely on a global narrative; if they do, inline the slice
  into each `task_spec`.
- **D4** two tuples `generator_task_ids` + `reducer_task_ids` (NOT a single `node_task_ids`).
- **D5** Task `summaries`→`outcomes` + `terminal_tool_result`; drop `fix_target_id`,
  `context_packet_id`, `task_center_attempt_id`, `spawn_reason`; `id`→`task_id`.
- **D6** reducer `prompt` required + nonblank.
- **D7** origin removed; all workflows generator-spawned (root = §6).
- **D8** bidirectional `child_workflow_id` ↔ `parent_task_id` (both kept).
- **D9** no closure abstraction; outcomes/status + the child-workflow resolution drive
  everything.
- **D10** `ancestry.py` dissolved into `terminal_routing.py`.
- **D11** three `state.py` → `_core/state.py`; `iteration/` package kept.
- **D12** `parent_task_id` added.
- **D13** `deps.py` removed (split to `launch.py` + `orchestrator.py`).
- **D14** handoff lifecycle = `start/apply/cancel_child_workflow`; "wake" retired.
- **D15** `GeneratorDagSummary`→`DagStatus`.
- **No-node decision:** drop the "node" concept; everything is a `task` by `role`, the plan
  is its DAG, `AttemptFailReason` collapses to `TASK_FAILED | STARTUP_FAILED`.
- **Flags:** FLAG-2 `task_id` = the identity (rename of `id`); FLAG-4 state target =
  `_core/state.py`; FLAG-5 `submit_workflow_handoff`; FLAG-6 (C1) root = synthetic run-level
  bootstrap generator (§6).

---

## 9. Invariants

- Every attempt has an exit gate (≥1 reducer) AND all work is judged (reachability) — both
  in `ordered_plan_tasks`, both regression-tested.
- Attempt immutability; retries re-plan; no cross-attempt memoization.
- TaskCenter is the control plane: no peer-to-peer comms; coordination is via state +
  outcomes + the single child-workflow resolution.
- Terminal tools called alone; reducer terminals binary.
- No task can be stranded: a `WAITING_WORKFLOW` generator always reaches DONE (resolve) or
  FAILED — via the orchestrator orphan-guard (M1) for plan tasks, and via §6's seed failsafe
  for the root.
- Concurrency/OCC unchanged for convergent reducers; a subset reducer may read the shared
  workspace mid-attempt (covered by a scenario).
- The run always terminates — on the happy path (bootstrap resolves → `finish_run`), the
  failed-root-workflow path, AND the failed-root-seed path (the §6 failsafe).

## 10. Verification scenarios (the regression guards)

Use `.venv/bin/pytest` + `.venv/bin/ruff`. The `task_center_runner` mock scenarios are the
integration harness. Add/keep:
- gate: `no_reducers` rejection (mirror `empty_tasks.py`), **reachability** rejection (a
  generator no reducer needs), multi-reducer partial-fail → retry, BLOCKED generator →
  attempt FAILED.
- relay/retry: deferral (iter N+1 = iter N reducer outcomes); failed-reducer feedback;
  **failed-generator-before-reducer** feedback.
- handoff: success; **failed-child** (failed outcomes + `fail_reason`); **deferred-multi-
  iteration child**; **M1** (start + cancel both fail → generator FAILED, not stuck);
  subset-reducer-with-sandbox-I/O.
- root (C1/COND-1) — three cases: end-to-end run → `finish_run` with run `outcomes` = root
  `workflow.outcomes`; failed-root-**workflow** → run `failed`; **failed-root-seed** (throw
  in seed/start) → run `failed`, bootstrap task not stranded `WAITING_WORKFLOW`.
- M2: a multi-task plan succeeds with only per-task `task_spec`s (no global narrative).
- round-trip: `Outcome` to/from record incl. legacy `"summary"`.

## 11. ADR

**Decision.** Replace evaluator with a general reducer that is a plan task; gate on plan
quiescence (`PLAN→RUN→CLOSED`); unify results under a recursive `outcomes` algebra and edges
under `needs`; remove the closure abstraction (outcomes + a single child-workflow resolution
drive the lifecycle); make every workflow generator-spawned (root = a synthetic run-level
bootstrap generator); consolidate the three `state.py` and dissolve `deps.py`/`ancestry.py`;
retire every `summary`; drop the "node" concept in favor of `task`.

**Drivers.** The five asks (needs / 3-recipes / unified-outcomes / coherent-semantics /
debt-paydown); the mock string-match coupling; a shared worktree.

**Consequences.** A reducer sees only its `needs` (a convergent reducer recovers the global
view); subset reducers introduce a mid-attempt sandbox-read shape (scoped, tested); retry +
failed-handoff feedback read failed *tasks* + `fail_reason`, not the canonical projection;
`final_outcome` is gone (`workflow.status` + derived `workflow.outcomes` replace it); the
root is a control shim, not a real agent (deliberate — uniform plumbing without the
agent-launch surface).

**Follow-ups (Tier-2 / staged).** Child `<task>`→`<outcome>` (+ `_task_xml.py`→
`_outcome_xml.py`); `tasks`→`generators`; R1d (`run_stage.py`→`orchestrator.py`); R1e
(`task_state.py`→`_core/state.py`); optional `WAITING_WORKFLOW`→`AWAITING_HANDOFF`.

## 12. Sequencing

1. WS1 (reducer foundation) → WS2 (gate + two tuples) → WS4 (outcomes) back-to-back.
2. WS5 (relay/retry) after WS4.
3. WS7 (closure removal + handoff + M1) after WS4; WS8 (root C1/COND-1) after WS7
   (`workflow.outcomes` must exist).
4. WS6 (state/deps/ancestry consolidation + store signatures) + WS10 (renames) land as
   deletions occur; verify imports at the end.
5. WS3 (verifier removal) parallel, targeting the WS2 reducer schema.
6. WS9 (mock/tests/migration/docs) continuous; the migration ops (C2) + scenarios are not
   optional.

**Parallel-agent note:** shared worktree — stage by explicit path; verify at HEAD.
