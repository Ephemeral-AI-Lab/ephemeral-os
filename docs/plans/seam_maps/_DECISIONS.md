# Reducers + Unified Outcomes Redesign — Consolidated DECISIONS

Synthesis of the 7 investigator seam maps (`cluster1_…` … `cluster7_…`) against
the authoritative spec `docs/plans/reducers_outcomes_redesign_PLAN.md` (HEAD
`fabce1b70`). The spec already resolves D1–D15; this document pins the items the
spec left unpinned, dedups the 7 overlapping file lists into one ownership map,
and fixes an importable core order. Names below are FINAL (end-state); the
"keep-old-then-rename" mechanics are sequencing notes in §3, not the table.

---

## 1. DECISIONS MANIFEST (settled names / signatures)

### 1.1 Outcome algebra (`_core/outcomes.py`, ← `generator_summaries.py`)

| Symbol | Final shape |
|---|---|
| `Outcome` (was `TaskOutcome`) | `@dataclass(frozen, slots)` `{ local_id: str, status: str, text: str\|None, children: tuple[Outcome,...]=(), failure: str\|None=None, raw_status: str\|None=None }`; keep `is_terminal`. Field `summary`→**`text`**. |
| `to_record(outcome)` | emits key **`text`** (not `summary`); recurses `children` (MN2). |
| `from_record(record)` | `text = record.get("text"); text = record.get("summary") if text is None else text` — **legacy `summary` fallback** for pre-migration rows (§10 round-trip). |
| `parse_outcomes_record(value)` (was `parse_achieved_record`) | JSON list → `[Outcome…]`; legacy free-text branch → `Outcome(local_id="summary", status="success", text=str(value))`. **Renamed** for vocab coherence. |
| `generator_outcomes(attempt, *, task_store)` | unchanged (over `attempt.generator_task_ids`). |
| `reducer_outcomes(attempt, *, task_store)` | **NEW**, mirrors `generator_outcomes` over `attempt.reducer_task_ids`. |
| `attempt_failure_line(attempt, *, task_store)` | rewritten: `STARTUP_FAILED → "agent_launch_failed"`; `TASK_FAILED →` render failed plan task(s) (role generic) over `generator_task_ids ∪ reducer_task_ids`. Internal `_failed_task_lines(...)` is the shared helper for this + WS5 retry + failure-aware iteration close. |
| `workflow_outcomes(workflow, *, iteration_store)` | **NEW** shared derivation = last iteration's `outcomes`. Used by run-report and root-close. |
| REMOVED | `latest_task_summary` (readers project `Outcome`; a private `_latest_text(rows)` may dedupe the 3 `summaries[-1]` walks — allowed deviation). `_handoff_rollup`/`child_outcomes_for_workflow` reshaped by WS7 (do not independently delete). |

### 1.2 Roles, IDs, terminals, submissions

| Item | Final |
|---|---|
| `AgentRole` / `TaskCenterTaskRole` | member `EVALUATOR`→**`REDUCER = "reducer"`**. `SpawnReason` enum **deleted** (D5). |
| task-id constructors (`_core/primitives.py`) | drop `evaluator_task_id`; add **`reducer_task_id(attempt_id, local_id) -> f"{attempt_id}:red:{local_id}"`** (two-arg, mirrors `generator_task_id`). Add **`root_task_id(run_id) -> f"{run_id}:root"`** and **`attempt_id_from_task_id(task_id) -> str\|None`** (parses the segment before `:gen:`/`:red:`/`:planner`; returns `None` for `:root`). These two are the SINGLE home for the parser that clusters 2/4/7 each needed (invariants prefix-check, depth-walk, root recognition, `_resolve_task_dir`). |
| reducer terminals (`tools/submission/reducer/`, ← `evaluator/`) | **`submit_reduction_success`** / **`submit_reduction_failure`** (binary). Input model field = **`summary: str = Field(..., min_length=1)`** — kept symmetric with `submit_execution_success` (verified both use `summary` today). **Drop** the `passed_criteria`/`failed_criteria` list args (criteria are gone — WS2). `submission_kind` metadata = `"reduction_success"`/`"reduction_failure"`. |
| handoff terminal | `submit_execution_handoff`→**`submit_workflow_handoff`**; input `SubmitWorkflowHandoffInput`; keep the `goal_handoff` arg name. |
| `_names.py` constants | drop `SUBMIT_VERIFICATION_{SUCCESS,FAILURE}_TOOL_NAME`; `SUBMIT_EVALUATION_{SUCCESS,FAILURE}_TOOL_NAME`→`SUBMIT_REDUCTION_{SUCCESS,FAILURE}_TOOL_NAME` (`"submit_reduction_success/failure"`); `SUBMIT_EXECUTION_HANDOFF_TOOL_NAME`→`SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME` (`"submit_workflow_handoff"`). (Plan omitted `_names.py` — it MUST be edited; `prompt.py` modules import these.) |
| `GeneratorSubmission` | `outcome`→**`status`**, `summary`→**`text`**, `payload`→**`terminal_tool_result`** (WS4 unified pass). |
| `EvaluatorSubmission`→`ReducerSubmission` | `{ attempt_id, task_id, status: Literal["success","failure"], text, terminal_tool_result }`. |
| `PlannedReducerTask` (submissions.py) | `@dataclass(frozen, slots)` `{ local_id: str, needs: tuple[str,...], prompt: str }` — **no `agent_name`** (reducer profile fixed; resolved at launch via `REDUCER_AGENT_NAME`). |
| `PlannedGeneratorTask` | `.deps`→**`.needs`**. `PlannerSubmission.tasks` field name KEPT (Tier-2 defers `tasks`→`generators`). |
| `PlannerSubmission` | drop `plan_spec`? **NO — KEEP `plan_spec`** (D3 keeps it as planner submission field for now; only `evaluation_criteria` removed, replaced by `reducers: tuple[PlannedReducerTask,...]`). |

> **Reducer schema — the load-bearing pin (clusters 1/2/6 all called it "unpinned").**
> The DTO is pinned by spec §2. What was actually open is the *tool input field* and the
> *mock builder*. RESOLUTION: tool input field = `summary` (symmetric with executor); the
> submit path builds `Outcome(local_id=local_id_of(task_id), status=present_status(status),
> text=summary)` and persists `outcomes=[to_record(o)]` + `terminal_tool_result`. Mock
> `reducer_response()` returns `{"summary": <text>}` (no criteria); `_reducer_script`
> emits `submit_reduction_success/failure(summary=…)`. **Mechanical reducer-block synthesis
> rule (cluster6 §0b), elevated to a hard rule for WF-B:**
> `reducer = {"id": "reduce", "needs": [t["id"] for t in tasks], "prompt": "\n".join(criteria) or "Confirm the plan tasks completed."}` — `needs=[all generator ids]` satisfies §1 reachability for every existing DAG.

### 1.3 Gate / stage / DAG (`attempt/state.py`→`_core/state.py`, `plan_dag.py`)

| Item | Final |
|---|---|
| `AttemptStage` | **`PLAN \| RUN \| CLOSED`** (drop `GENERATE`/`EVALUATE`; `RUN="run"`). |
| `AttemptStatus` | `RUNNING \| PASSED \| FAILED` (unchanged). |
| `AttemptFailReason` | **`TASK_FAILED="task_failed" \| STARTUP_FAILED="startup_failed"`** (role of failed task says which). `apply_planner_failure` uses `TASK_FAILED`; `_mark_startup_failed` uses `STARTUP_FAILED`. |
| `Attempt` tuples | drop `evaluation_criteria`, `evaluator_task_id`; add **`reducer_task_ids: tuple[str,...]`** beside `generator_task_ids` (C2). KEEP `plan_spec`. |
| `ordered_generator_tasks`→`ordered_plan_tasks` | signature **`(generators, reducers) -> (ordered_generators, ordered_reducers)`** (two args, two returns; reachability needs the gen/reducer discriminator). Validates: unique ids across both, known needs (gen or reducer target), no cycle, **≥1 reducer**, **reachability** (every generator in the reverse-`needs`-closure of ≥1 reducer). |
| `dependency_task_ids` | **DELETE**; the combined `local_id→task_id` map is built inline in `orchestrator._persist_*` where both tuples are in scope. |
| `ready_pending_generator_ids`→**`ready_pending_plan_ids`** | logic unchanged (role-agnostic over the record list). |
| `GeneratorDagSummary`→**`DagStatus`** / `summarize_generator_dag`→**`dag_status`** | pure rename (D15). |
| `AttemptStageAdvancer` class | **KEEP the class name**; module `stage_advancer.py`→`run_stage.py` is the rename mandate. |
| RUN task sourcing | loop `get_task(tid)` over `generator_task_ids ∪ reducer_task_ids` (no new batch method; naturally excludes planner). `list_generator_tasks_for_attempt` + its protocol entry **DELETED** (sole consumer was the old generator stage). `list_tasks_for_attempt` KEPT (live consumer `runner.py:95`); WS6 re-derives its filter by `id.like(f"{attempt_id}:%")`. |
| `assert_evaluator_task_for_submission`→`assert_reducer_task_for_submission`; `assert_task_belongs_to_attempt` | rewrite the `task_center_attempt_id` read to **`str(task.get("id") or "").startswith(f"{attempt.id}:")`** (forward-safe before WS6 column drop). |

### 1.4 Closure removal / handoff / root (`_core/state.py`, `run_controller.py`)

| Item | Final |
|---|---|
| `Workflow` | `goal`→**`workflow_goal`**; ADD **`parent_task_id: str\|None`**; DROP `final_outcome`, `origin_kind`, `requested_by_task_id`, `.origin`. Field id stays `.id` (DTO) — see asymmetry note. |
| `Iteration` | `goal`→**`iteration_goal`**; `task_summary`→**`outcomes`** (JSON-string in a Text column); DROP `plan_spec`. |
| DELETED classes | `WorkflowOriginKind`, `WorkflowOrigin`, `WorkflowClosureReport(+to_final_outcome)`, `WorkflowClosureDeliveryResult`, `IterationClosureReport`, `ClosureOutcome`, `TerminalSuccess`/`SuccessDeferred`/`AttemptPlanFailed`, `SpawnReason`, `AttemptDelegatedWorkflowParentTask`. Files DELETED: `closure_report_router.py`, `workflow/ancestry.py`, `attempt/deps.py`, the 3× `state.py`. |
| handoff lifecycle (3 orchestrator methods, no wrapper class, no "wake") | **`start_child_workflow(*, generator_task, child_workflow)`** (atomic `RUNNING→WAITING_WORKFLOW` + `child_workflow_id`); **`apply_child_workflow_outcome(*, generator_task, child_workflow, final_attempt_id)`** (write generator `outcomes` = one `Outcome` whose `children = child_workflow.outcomes` (MN2), DONE/FAILED, advance DAG); **`cancel_child_workflow(*, generator_task)`** (restore RUNNING). |
| M1 orphan-guard | state-level last resort: if start/cancel fail, force `WAITING_WORKFLOW→FAILED` via `set_task_status_if_current(expected=WAITING_WORKFLOW, status=FAILED)` with empty/failed outcomes. |
| `child_workflow_id` write path | **add `child_workflow_id: str\|None=None` param to `set_task_status_if_current`** (concrete store + `TaskStoreProtocol`) so the flip+link is one transaction (no dedicated method). |
| root recognition | `parent_task_id == root_task_id(run_id)` (i.e. ends `:root`) for the close fork; `attempt_id_from_task_id` for the depth walk. |
| `RunController` public API (`run_controller.py`, NEW) | `RunController(*, runtime: AttemptDeps, …)`; **`start_root_run(*, prompt: str, task_center_run_id: str) -> StartedWorkflow`** (seed synthetic GENERATOR `root_task_id` `status=RUNNING`, then `WorkflowStarter.start(parent_task_id=root_task_id)`; any throw → `_finish_run_if_open(run_id, status="failed")`+re-raise); **`on_root_workflow_closed(*, child_workflow: Workflow) -> None`** (idempotent; write bootstrap `outcomes=child_workflow.outcomes`, mark DONE/FAILED, `finish_run`); private `_finish_run_if_open(run_id, *, status)` (moved here from `bootstrap.py:207-210`). |
| `WorkflowStarter.start` | signature `(*, prompt, parent_task_id: str)` — single path (no `WorkflowOrigin`). Relaxes the `:143` attempt-bound + `:171` RUNNING guards for the `:root` parent. Injected into `WorkflowLifecycle`: a `run_close_handler` callback (mirrors the deleted `deliver_closure_report` seam) for the root branch; registry lookup for the attempt branch. |

### 1.5 The consolidated `_core/state.py` (D11)

Absorbs `Workflow` + `Iteration` + `Attempt` + the 6 lifecycle enums
(`WorkflowStatus`, `IterationStatus`, `IterationCreationReason`, `AttemptStage`,
`AttemptStatus`, `AttemptFailReason`). `task_state.py` stays separate (R1e is a
post-green optional fold). Importers (`outcomes.py`, `invariants.py`,
`persistence.py`, the 3 coordinators, recipes, stores) repoint to `_core.state`.
The 3 old `*/state.py` paths keep **temporary re-export shims** during migration
(see §3) so steps stay importable without one giant repoint.

### 1.6 Recipes / context engine

| Item | Final |
|---|---|
| `recipes/evaluator.py`→`reducer.py` | **shape change**: per-task (`_REQUIRED_FIELDS={"workflow_id","attempt_id","task_id"}`), reads ONLY its `needs` outcomes + its own prompt (in `task.context_message`). No `plan_spec`/`evaluation_criteria`/all-generator reads. |
| `recipes/_needs.py` (NEW) | `needs_outcome_blocks(*, needs: tuple[str,...], task_store) -> list[ContextBlock]` — extracted from generator `_dependency_blocks`; `group_tag`/`group_id` `"dependency"`/`"dependencies"`→**`"needs"`**; child tag stays `"task"`. Shared by generator + reducer. |
| `recipes/generator.py` | delete the dead `attempt.plan_spec` `<plan_spec>` block; call `needs_outcome_blocks`. |
| `recipes/planner.py` | R1a: fold in `iterations.py` + `attempts.py` (then DELETE both). Retry body renders failed-TASK outcomes (any role) + `<failure>`; **drop `<evaluator_summary>`** / `_evaluator_summary_if_ran`. |
| `context_engine/core.py`→`engine.py` | module rename; repoint 6 src + lazy `_EXPORTS`. |
| `ContextScope.for_evaluator`→`for_reducer` | gains `task_id` (mirror `for_generator`). |
| tag_dictionary / renderer | delete `plan_spec`/`evaluation_criteria`/`evaluator_summary` descriptors + `_DEFAULT_TAGS["task_specification"]`; `dependency`→`needs`; add `assigned_prompt`. Reducer prompt block reuses `PLANNED_TASK_SPEC` kind with `metadata["tag"]="assigned_prompt"` (no enum churn). Drop dead `ContextBlockKind.TASK_SPECIFICATION`. |
| `AGENT_DIRECTIVES` | drop `verifier`; `evaluator`→`reducer` ("Digest your <needs> and gate against <assigned_prompt>."). |

### 1.7 DB migration maps (verbatim, cluster7) — representation asymmetry pinned

```python
_DROPPED_COLUMNS = {
    # keep existing agent_runs / task_center_runs
    "task_center_tasks": { ...existing ("summary"+11 legacy)...,
        "fix_target_id", "context_packet_id", "task_center_attempt_id", "spawn_reason" },
    "attempts":   {"evaluation_criteria", "evaluator_task_id", "plan_spec"},   # 3 cols (DRIFT-A: plan WS2 listed 2)
    "iterations": {"plan_spec"},
    "workflows":  {"final_outcome", "origin_kind", "requested_by_task_id"},
}
_RENAMED_COLUMNS = {
    "iterations":        {"task_summary": "outcomes"},   # REPLACES the obsolete task_specification→plan_spec
    "task_center_tasks": {"summaries": "outcomes"},
    # attempts: REMOVE task_specification→plan_spec (plan_spec dropped, not renamed)
}
```
- New columns (`attempts.reducer_task_ids`, `workflows.parent_task_id`,
  `task_center_tasks.terminal_tool_result`, `task_center_tasks.child_workflow_id`)
  are **ADDs** handled by `_add_missing_columns` from the ORM — NOT renames.
- **DB model PK column stays `id`** for all 4 records; `id`→`task_id` is the
  **serialized-dict key + store kwarg + DTO field only** (OD-1). No PK column rename.
- **Representation asymmetry (do not unify):** `Task.outcomes` = a **JSON column**
  (list of Outcome records); `Iteration.outcomes` = **Text holding a JSON string**
  (`json.dumps`'d by the coordinator, `parse_outcomes_record(json.loads(...))` back).

---

## 2. M2 RESOLUTION (planner.md / executor.md)

**Verdict: BOTH (a) mandatory schema/prose drop AND (b) distribute (not duplicate).**

**(a) is forced by the schema, independent of framing.** `plan_spec` and
`evaluation_criteria` are planner SUBMISSION FIELDS (`planner.md:60,64,82`) that
WS2 removes. So planner.md's terminal **signatures, field list, hard-validity
rules, and output-discipline prose** must drop them regardless.

**(b) DISTRIBUTE the global narrative — never paste it.** ADR §11 (a reducer sees
only its `needs`) + §5 (generator symmetric with reducer) mandate self-contained
tasks. The framing `plan_spec` carried decomposes into:
1. each **`task_spec`** (the generator's only framing — strengthen the existing
   "write each task_spec so the agent can act without re-reading the contract");
2. each **reducer `prompt`** (the per-gate acceptance authority, replacing
   `evaluation_criteria`).
Pasting one narrative into N task_specs is **forbidden** — it contradicts
`planner.md:114` "don't paste content".

**Concrete planner.md edits (C6):**
- L60/64 signatures → `submit_plan_closes_goal(tasks, reducers)` (no `plan_spec`,
  no `evaluation_criteria`).
- L82 DELETE the `plan_spec` field bullet; L83-85 DELETE `evaluation_criteria`;
  ADD a `reducers` field bullet (each `{id, needs, prompt}`, `prompt` = gate authority).
- L86-93 `tasks` items `{id, agent_name, deps}`→`{id, agent_name, needs}`; drop
  `verifier` from agent choices (WS3).
- L44 `<iteration status=…>`→`position=…` (prompt was already stale vs code);
  L46 delete the stale `<status_summary>/<failed_criteria>/<passed_criteria>` list;
  describe failed-task outcomes + `<failure>`.
- L104/110/113/114/122 `evaluator`→`reducer`, drop `plan_spec`/`evaluation_criteria`.
- ADD one sentence: "Each reducer's `prompt` is the acceptance authority for the
  slice it gates; scope it to what its `needs` produce."

**executor.md (P5):** body has **no** `plan_spec` dependence — only vocab
(`evaluator`→`reducer`, `submit_execution_handoff`→`submit_workflow_handoff` at
L26/37/39/51/52). **WS9 blocker (outside recipe read-set):** `config/skills/
executor/SKILL.md:9,17` and `config/skills/evaluator/SKILL.md:9` DO reference
`<plan_spec>`/`<evaluation_criteria>` and must be reframed to self-contained
`<needs>`/`<assigned_task>`/`<assigned_prompt>`; `skills/evaluator/`→`reducer/`
dir rename (else loader hard-fails on the `reducer.md` skill path). Owned by the
Step-1/WS9 lane, flagged.

**M2 verification scenario:** a multi-task plan succeeds with per-task `task_spec`s
only (no global narrative) — spec §10.

---

## 3. CORE IMPLEMENTATION ORDER (dependency-ordered, each step importable)

Constraint spine: **type roots first → store/protocol signatures BEFORE callers →
closure/handoff/root LAST (needs `workflow.outcomes`+`parent_task_id`)**. Module
renames repoint importers in-step; the old `*/state.py` paths keep re-export shims
until Step 7 deletes them.

**Step 1 — Reducer foundation (WS1 + WS3).** `agents/definition/model.py` +
`loader.py` (`EVALUATOR`→`REDUCER`); `_core/task_state.py` (role + delete
`SpawnReason`); `_core/primitives.py` (`reducer_task_id`, `root_task_id`,
`attempt_id_from_task_id`; drop `evaluator_task_id`); `tools/submission/reducer/`
(rename pkg, `submit_reduction_{success,failure}`, input field `summary`); DELETE
`tools/submission/verifier/`; `tools/_names.py`; `_factory.py`;
`_terminals/registry.py`; `submit_execution_handoff`→`submit_workflow_handoff`;
profiles `evaluator.md`→`reducer.md`, DELETE `generator_verifier.md`,
`executor.md` vocab; `executor_routing.py`; `bootstrap._REQUIRED_AGENT_NAMES`;
`skills/evaluator/`→`reducer/`. *Leaves the package importable; reducer terminals
call a stubbed `apply_reducer_submission` until Step 4.*

**Step 2 — State consolidation + enum/tuple shapes (WS6 D11 + WS2/WS4 field
bodies).** Author `_core/state.py` with `Workflow`(+`parent_task_id`,
`workflow_goal`; −origin/final_outcome), `Iteration`(`iteration_goal`,
`outcomes`; −plan_spec), `Attempt`(+`reducer_task_ids`; −`evaluation_criteria`/
`evaluator_task_id`; keep `plan_spec`), and the 6 enums (`AttemptStage PLAN/RUN/
CLOSED`, `AttemptFailReason TASK_FAILED/STARTUP_FAILED`). Leave **re-export shims**
at `workflow/state.py`, `iteration/state.py`, `attempt/state.py`. Repoint
`_core` importers. *Importable: shims keep old paths alive.*

**Step 3 — Outcome type root (WS4 type).** `generator_summaries.py`→`outcomes.py`:
`Outcome`/`text`, `from_record` legacy fallback, `parse_outcomes_record`, add
`reducer_outcomes`, `workflow_outcomes`, rewrite `attempt_failure_line` over
`TASK_FAILED` (now defined). Repoint the 4 src + import-only consumers. *Type root
stable before any writer.*

**Step 4 — Store + protocol signatures (MN3) — BEFORE callers.** `db/engine.py`
(migration maps §1.7); `db/models/{workflow,iteration,attempt,task_center}.py`
(column drops/adds; PK stays `id`); `db/stores/*` + `_core/persistence.py`
Protocols: `upsert_task` (`summaries`→`outcomes`+`terminal_tool_result`; drop
`task_center_attempt_id`/`fix_target_id`/`spawn_reason`/`context_packet_id`),
`set_task_status*` (replace-write `outcomes`+`terminal_tool_result`; **add
`child_workflow_id`** to `set_task_status_if_current`), `close_succeeded`
(`task_summary`→`outcomes`), `set_status` (drop `final_outcome`),
`set_evaluator_task_id`→`set_reducer_task_ids`, `set_plan_contract` (drop
`evaluation_criteria`), workflow `insert(parent_task_id)`; delete
`set_task_context_packet_id` and `list_generator_tasks_for_attempt`; re-derive
`list_tasks_for_attempt` by `id.like('{attempt}:%')`. Audit `recorder.py`/
`node_id.py` role sets + serializer key renames (lands WITH model drops or
AttributeError). *Callers in Steps 5–7 now see the final signatures.*

**Step 5 — Gate + submit path (WS2 + WS4 writes).** `plan_dag.py` (←
`generator_dag.py`: `ordered_plan_tasks`, `dag_status`/`DagStatus`,
`ready_pending_plan_ids`, delete `dependency_task_ids`); `planner/_schemas.py`
(`needs`, `ReducerInput`, `reducers` min 1); `submissions.py`
(`PlannedReducerTask`, `ReducerSubmission`, `.needs`); `run_stage.py` (←
`stage_advancer.py`: single RUN advance over both tuples, delete evaluator-stage
methods); `orchestrator.py` (persist reducers, both tuples, stage RUN,
`apply_reducer_submission`, `_write_submission_status` writes `outcomes`+
`terminal_tool_result`, inline `local_id→task_id` map); `orchestrator_registry.py`
Protocol (`apply_reducer_submission`); `_core/invariants.py` (prefix-check +
reducer assert); `__init__.py` facade. Coordinator `attempt_coordinator.py`
`_iteration_outcomes_for` (reducer projection, failure-aware close). *Reducer
terminals now reach the real `apply_reducer_submission`.*

**Step 6 — Recipes + prompts (WS5 + M2).** `recipes/_needs.py`, `reducer.py`,
`generator.py`, `planner.py` (R1a fold), `_task_xml.py`, `core.py`→`engine.py`,
scope/tag_dictionary/renderer/packet/directives; `planner.md` (§2 M2), `reducer.md`.

**Step 7 — Closure removal + handoff + root (WS7 + WS8) — LAST.** Delete
`closure_report_router.py`, `ancestry.py`, `deps.py`, gut `workflow/state.py` →
**remove the re-export shims** from Step 2; `lifecycle.py` close-routing fork;
`starter.py` (`parent_task_id`, atomic flip+link, M1 guard); 3 orchestrator
handoff methods; `terminal_routing.py` (← `terminal_tool_routing.py`, fold
`nested_workflow_depth` via `attempt_id_from_task_id`); author `run_controller.py`;
`bootstrap.py` rewire; `workflow_store.py`/`workflow.py` parent_task_id; run-report
`_graph_summary` (`workflow.outcomes`/`parent_task_id`). *Final import sweep:
grep finds none of the old module/symbol names.*

> 8-step variant if Step 4 is too large: split DB-models+engine (4a) from
> stores+protocol (4b). Either keeps "signatures before callers".

**Sequencing note (final names vs transient):** WS2 may keep
`GeneratorSubmission.outcome`/`summary` momentarily, but the WS4 unified pass in
Step 5 renames `outcome→status`, `summary→text`, `payload→terminal_tool_result`
across Generator+Reducer in ONE pass — the table in §1 records only the final
names. The reducer tool *input field* is `summary` permanently (it is the tool
arg, mapped to `Outcome.text` in the submit path).

---

## 4. WF-B PARTITION (disjoint single-owner mechanical groups)

Union rule applied: **a file classified CORE in ANY cluster is core (Steps 1–7),
never WF-B.** Discriminator for unit tests: symbol/string-rename-only → WF-B;
assertion encodes changed behavior → core-coupled (lands with its Step). This
guarantees no file in two groups and no core leak.

**Explicitly NOT WF-B (core-coupled, land with their Step):**
`test_lifecycle/**`, `test_domain/**`, `test_persistence/**`, `test_agent_launch/**`,
`test_audit/**` (assert DTO/lifecycle behavior — WS1/2/4/6/7);
`test_iteration_attempt_coordinator.py` (generator→reducer projection flip, Step 5);
`test_generator_dag.py` (new ≥1-reducer/reachability assertions, Step 5);
context-engine `test_attempts*.py`/`test_recipes_*.py`/`test_engine.py`/
`test_scope.py`/`test_packet.py` (Step 6); `test_domain/test_ancestry.py`,
`test_iteration_closure_report.py` (deleted, Step 7).

| Group | Owns (files) | Transforms |
|---|---|---|
| **G1 — pipeline DAG scenarios** | `scenarios/pipeline/{dependency_dag_diamond,dependency_dag_parallel,dependency_dag_serial,dependency_dag_mixed,dependency_blocked_descendants,generator_failure_quiescence}.py` | inline `deps`→`needs`; `evaluation_criteria`→`reducers` (§1.2 rule); evaluator terminals/response→reducer; `failed_criteria` path stays (asserts FAIL). |
| **G2 — pipeline lifecycle/retry** | `scenarios/pipeline/{initial_workflow,iterative_deferral,attempt_budget_exhausted,attempt_retry_generator_failure,attempt_retry_planner_failure}.py` + `attempt_retry_evaluator_failure.py`→**rename** `attempt_retry_reducer_failure.py` (class `AttemptRetryEvaluatorFailure`→`…Reducer…`, `.name`, importers) + `pipeline/__init__.py` | + `fail_reason="*_failed"`→`"task_failed"`; `plan_shapes` param rename. |
| **G3 — nested/deferred + messages** | `scenarios/pipeline/{nested_workflow,deferred_parent_planner_terminal_routing,initial_messages_capture}.py` | 2 classes + 3 inline `evaluation_criteria` dicts in nested; `recursive_handoff_goal` text stays. |
| **G4 — planner_validation** | `scenarios/planner_validation/{cycle_in_deps,defers_without_deferred_goal,duplicate_local_id,empty_tasks,unknown_agent_name,unknown_dep}.py` + `__init__.py` | `deps`→`needs`; rejection messages. (NEW `no_reducers`/reachability-reject scenarios are CORE-authored in Step 5, NOT here.) |
| **G5 — top-level + correctness** | `scenarios/{full_case_user_input,full_stack_adversarial,correctness_testing,user_input,lifecycle}.py` + `scenarios/__init__.py` | verifier→executor+reducer rework (WS3); registry. |
| **G6 — sandbox scenarios** | `scenarios/sandbox/{auto_squash_commit_resume,background_shell,complex_project_build,complex_project_build_grep_glob,complex_project_build_shell_edit_lsp,ephemeral_workspace,heavy_io_zoned_concurrent,high_concurrency_layerstack_overlay_occ,occ_concurrent_conflicts,plugin}.py` + `__init__.py` | uniform `evaluation_criteria`/`deps`/`evaluator_response` renames. |
| **G7 — context-engine `<dependency>`→`<needs>` test asserts** | `test_context_engine/{test_renderer,test_tag_dictionary,test_context_outline,test_task_guidance,test_role_context_matches_diagram,test_recipes_other}.py` | `<dependency>`→`<needs>` string asserts. **Run AFTER Step 6** (recipe must emit `<needs>`). |
| **G8 — mock asserting tests** | `tests/mock/task_center/{test_focused_scenarios,test_correctness,test_full_case_user_input,test_initial_messages_capture,test_deferred_parent_planner_terminal_routing}.py`, `tests/mock/sandbox/full_stack/test_full_stack_adversarial.py`, `tests/mock/sandbox/project_build/test_project_build_shell_edit_lsp_three_parallel_agents.py`, `tests/mock/sandbox/capacity/test_capacity_scenario_packs.py`, `tests/mock/{_focused_scenario_contracts,_project_build_contracts}.py` | assert `evaluator`/`evaluation_criteria`/`task_summary`/`summaries`/handoff string-matches → reducer/outcomes/`parent_task_id`. |
| **G9 — mock contract/import hub + capacity** | `tests/mock/contracts/{test_scenario_suite_imports,test_runner_imports,test_context_message_scenarios,test_scenario_loop_runner_planner_submit,test_correctness_via_event_source,test_scenario_event_source_spike,test_advisor_gate_negative_path}.py`, `scenarios/capacity/{pack_catalog,__init__}.py`, `tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py` | hub renames; `test_scenario_loop_runner_planner_submit.py` has its own inline scenario; `pack_catalog.py:138 context.evaluator_iterative_deferral`→`context.reducer_…` in lockstep with Step 6. |
| **G10 — WS1/WS3 vocab test propagation** | `tests/unit_test/test_agents/{test_routing_acceptance,test_agent_markdown,test_profile_routing,test_planner_profile_md,test_helper_profile_identity_sentences}.py`, `test_agents/test_verifier_evaluator_edit_tools.py`→rename, `test_tools/{test_submission_tool_registration,test_submission_terminal_routing,test_submission_soft_reminders,test_ask_advisor_retry,test_schema_summary,conftest}.py`, `test_tools/test_terminals/test_descriptor_registry.py`, `test_tools/test_hooks/{test_iws_gate_wiring,test_require_no_inflight_background_tasks}.py`, `test_tools/test_submission/test_advisor_approval_prehook.py`, `contracts/test_tool_intent_drift.py`, `test_benchmarks/{test_sweevo_mock_agent_execution,test_sweevo_audit_recorder,test_sweevo_snapshot_verifier}.py`, mock `tests/mock/{_project_build_contracts,_focused_scenario_contracts,contracts/test_advisor_gate_wiring}.py`, `agent/mock/scenario_adapter.py`-asserting `test_advisor_gate_*` | symbol/string renames only (`submit_evaluation`/`verification`→`reduction`, `EVALUATOR`→`REDUCER`, handoff name). Lands after Step 1. |

**CORE shared mock seams (NOT WF-B — Step 1/5/6 core lane):** `scenarios/base.py`
(`evaluator_response`→`reducer_response`, delete `verifier_response`),
`agent/mock/scenario_adapter.py` (`_reducer_script`, delete `_verifier_script`,
role dispatch, handoff rename ×2), `agent/mock/scenario_loop_runner.py`
(`_inspect_prompt` role branches), `_scenario_helpers/plan_shapes.py` (reducer-block
synthesis rule), `capacity/full_system_capacity_matrix.py` (dynamic plan mutation),
`test_task_center/conftest.py` (shared fixture: reducer agent + delete verifier +
handoff rename — coordinated with Step 1 `AgentRole`),
`_scenario_helpers/workflow_origin.py` (origin removed → `parent_task_id.endswith(":root")`).

---

## 5. OPEN DECISIONS + DRIFT (consolidated, with resolution)

### Resolved open decisions

| # | Question | Resolution |
|---|---|---|
| OD-A | Reducer tool input field / mock builder (c1-OD1, c2-#4, c6-#1) | input field **`summary`** (symmetric w/ executor); submit path → `Outcome.text`; mock `reducer_response()→{"summary":…}`; reducer-block rule `needs=[all gen ids]`. |
| OD-B | `parse_achieved_record` fn name | rename **`parse_outcomes_record`**. |
| OD-C | `latest_task_summary` removal | remove; private `_latest_text(rows)` helper allowed (dedupes 3 walks). |
| OD-D | `ordered_plan_tasks` signature | `(generators, reducers)→(ordered_gen, ordered_red)`. |
| OD-E | `dependency_task_ids` | DELETE; inline combined `local→id` map in orchestrator. |
| OD-F | `ready_pending_generator_ids` name | **`ready_pending_plan_ids`**. |
| OD-G | `AttemptStageAdvancer` class name | KEEP (only module renamed). |
| OD-H | `ReducerSubmission` status field | final **`status`** (WS4 unified pass, Step 5); WS2 may transit on `outcome`. |
| OD-I | `PlannedReducerTask` shape | `{local_id, needs, prompt}`, no `agent_name`. |
| OD-J | `ReducerInput` schema id field | `id` (schema) → `local_id` (DTO). |
| OD-K | RUN sourcing | loop `get_task` over both tuples; no batch method. |
| OD-L | Failure-aware `iteration.outcomes` shape | list of failed-task `Outcome` (`status="failure"`, `failure=<fail_reason line>`) — shared with WS5 retry. |
| OD-M | `Iteration.outcomes` column type | **Text** (JSON string); asymmetric w/ `Task.outcomes` JSON column. |
| OD-N | `Task.agent_run_id` | keep existing `agent_run` relationship; no new column. |
| OD-O | `Task.id`→`task_id` PK | dict-key/DTO/kwarg only; **column stays `id`** (no PK rename). |
| OD-P | `set_task_status*` semantics | replace-write `outcomes`+`terminal_tool_result` (terminal task has one result). |
| OD-Q | failure-aware close API | extend `set_status` with optional `outcomes` kwarg (no new `close_failed`). |
| OD-R | `workflow.outcomes` derivation | shared `workflow_outcomes()` in `outcomes.py` (run-report + root path). |
| OD-S | `child_workflow_id` write path | add param to `set_task_status_if_current` (no dedicated method). |
| OD-T | `requested_by_task_id`→`parent_task_id` | drop+add (per plan; dev DBs empty). |
| OD-U | `_finish_run_if_open` ownership | move into `RunController`. |
| OD-V | root recognition + parser home | `root_task_id`/`attempt_id_from_task_id` in `primitives.py`. |
| OD-W | reducer assigned-prompt block kind | reuse `PLANNED_TASK_SPEC` + `metadata["tag"]="assigned_prompt"`. |
| OD-X | M2/D3 | BOTH (a) mandatory drop + (b) distribute (not paste) — §2. |
| OD-Y | `plan_shapes.py` param | rename `evaluation_criteria`→`criteria`. |
| OD-Z | NEW gate scenarios (`no_reducers`, reachability-reject, multi-reducer partial-fail, BLOCKED→FAIL) | **CORE-authored in Step 5** (WS2 core), NOT WF-B. |

### Open items still needing a human/owner call

- **WS9 SKILL.md files** (`config/skills/{executor,evaluator}/SKILL.md`): drop
  `<plan_spec>`/`<evaluation_criteria>` refs, rename `evaluator/`→`reducer/` dir
  (loader hard-fails otherwise). Outside the recipe read-set; assign to Step 1/WS9.
- **Planner rendered goal tag** `<goal>` vs `<workflow_goal>`/`<iteration_goal>`
  (D2): the `_inspect_prompt` checks (loop_runner :250,258) must match whatever
  Step 6 recipe emits — pin during Step 6.

### Drift (verified; corrections to apply)

- **DRIFT-A (load-bearing):** `_DROPPED_COLUMNS["attempts"]` must include **3**
  cols `{evaluation_criteria, evaluator_task_id, plan_spec}` (plan WS2 listed 2);
  the `attempts` key does **not** exist today — ADD the whole key (not `+=`).
- `_RENAMED_COLUMNS`: REMOVE the obsolete `task_specification→plan_spec` entries
  for iterations+attempts (plan_spec dropped); ADD `task_summary→outcomes` +
  `summaries→outcomes`. No `_RENAMED_TABLES` exists (memory note stale for this file).
- **`tools/_names.py` is NOT in the plan but MUST be edited** (constants consumed
  by `prompt.py` modules) — Step 1.
- `list_generator_tasks_for_attempt`/`list_tasks_for_attempt` filter on
  `task_center_attempt_id` (dropped by D5): RUN sources by id; delete the former,
  re-derive the latter (live consumer `runner.py:95`).
- `assert_task_belongs_to_attempt` reads the dropped column → rewrite to task-id
  prefix check (Step 5; forward-safe before Step 4 drop).
- `RegisteredAttemptOrchestrator` Protocol (`orchestrator_registry.py:25-33`)
  declares BOTH `apply_workflow_closure_report` AND `apply_evaluator_submission` —
  rename to the 3 handoff methods (Step 7) + `apply_reducer_submission` (Step 5);
  plan omitted it.
- deps.py importer list omits `tools/submission/context/attempt.py:11` and
  `attempt/launch.py:16` (both real `AttemptDeps`/`AgentLaunch` importers).
- `model.py` AgentRole docstring/field-comment assert "GENERATOR covers executor
  and verifier" — stale after WS3; GENERATOR is executor-only.
- TWO independent `_serialize_task` serializers (`task_center_store.py` +
  `audit/recorder.py`) + 3 more `_serialize_*` in recorder — all need the same
  key renames (plan flagged only one).
- `runner.py` workflow.outcomes surface is `_graph_summary` **L127-137**, not
  130-133; `orchestrator._write_submission_status` is **327-349**; loader role
  error string is **L65-70**.
- audit files live under `task_center_runner/audit/`, NOT `task_center/audit/`
  (which does not exist); `_core/audit.py` is a separate smaller emitter.
