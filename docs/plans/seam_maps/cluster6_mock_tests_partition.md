# Cluster 6 — WS9 Mock harness + scenarios + asserting tests (WF-B partition)

**Scope:** `backend/src/task_center_runner/scenarios/**`, `…/agent/mock/scenario_adapter.py`,
`…/agent/mock/scenario_loop_runner.py`, `…/scenarios/_scenario_helpers/plan_shapes.py`,
`backend/src/task_center_runner/tests/mock/**` (the asserting mock-contract tests), and
`backend/tests/unit_test/test_task_center/test_context_engine/**` `<dependency>`→`<needs>`
assertions. Baseline: 428 unit tests pass (ignore the 1 pre-existing
`test_attempt_harness_records_runner_token_usage` failure — not ours).

**Primary deliverable:** a DISJOINT single-owner file partition for parallel mechanical
propagation (WF-B), plus the small set of CORE shared seams that must be hand-edited and
must NOT be in any WF-B group.

---

## 0. The CORE shared seams (hand-edited; NOT in WF-B). Do these FIRST.

Every scenario file calls a hook method (`evaluator_response`→`reducer_response`) and
imports reducer terminals; every group's tests depend on shared fixtures. These four+two
files are the contract surface — they must land before WF-B groups run, or the partition
breaks. They are listed in `core_files` and are owned by the cluster-6 core lane, not WF-B.

| File | Why CORE (hand-edited logic) | Target |
|---|---|---|
| `…/scenarios/base.py` | Defines the `Scenario` protocol + `ScenarioBase` hook names every scenario overrides. `verifier_response` + `evaluator_response` (lines 52-54, 73-77) are the method contract. | Rename `evaluator_response`→`reducer_response`; **delete** `verifier_response` (verifier profile gone, WS3). Update module docstring (lines 1-6) "planner/executor/verifier/evaluator". Keep `ScenarioContext` dataclass as-is. |
| `…/agent/mock/scenario_adapter.py` | Role-dispatch loop + script builders + `submit_execution_handoff` emission. Hand logic, not vocab. | `_evaluator_script`→`_reducer_script` calling `scenario.reducer_response` (lines 136-139); **delete** `_verifier_script` (142-145); dispatch `role == "evaluator"`→`"reducer"` and **delete** `role == "verifier"` branch (lines 296-300); `submit_execution_handoff`→`submit_workflow_handoff` ×2 (lines 203, 205); docstrings (lines 4, 9, 275-276). NB: handoff lives ONLY here — scenarios pass `recursive_handoff_goal` text. |
| `…/agent/mock/scenario_loop_runner.py` | `_inspect_prompt` string-matches the rendered context XML per role. Hand logic (branching by role + checks dict). | See §0a below — the precise anchors. |
| `…/scenarios/_scenario_helpers/plan_shapes.py` | Shared plan-dict factories (`minimal_full_plan`, `preflight_full_plan`, `preflight_defers_plan`) consumed by many scenarios. The `evaluation_criteria`→`reducers` **shape** change lives here once; mechanical for callers after. | See §0b. |
| `backend/tests/unit_test/test_task_center/conftest.py` | SHARED fixture: registers `evaluator` agent (`role=AgentRole.EVALUATOR`, `context_recipe="evaluator"`, `terminals=["submit_evaluation"]`, lines 180-189) + `verifier` (190-199); used by ALL lifecycle/domain/persistence tests; touches `AgentRole.EVALUATOR` (WS1 core) and `submit_execution_handoff` (line 164). | Rename evaluator→reducer agent (`AgentRole.REDUCER`, `context_recipe="reducer"`, reducer terminals); delete verifier agent; `submit_execution_handoff`→`submit_workflow_handoff` (line 164). MUST coordinate with WS1 `AgentRole` enum + WS9 conftest. Hand-edited. |
| `…/scenarios/capacity/full_system_capacity_matrix.py` | Mutates the plan dict dynamically at runtime (`capacity_metrics_summary` task injection, `deps` rewiring, `evaluation_criteria` append — lines 35-51). Not a static dict; needs hand logic to keep reachability after the reducer block lands. | Hand-edit the dynamic plan-mutation to the `reducers`/`needs` shape; keep the injected sink task wired so the reducer's `needs` still reaches it. |

`…/tests/mock/conftest.py` and `…/tests/conftest.py` have **zero** vocab hits — leave them.

### 0a. `scenario_loop_runner.py` `_inspect_prompt` — exact anchors (CORE, verified)

Plan §WS9 cited `:303`, `:274,302`; current code differs — **DRIFT, use these**:
- line **250, 258**: `"<goal>" in prompt` (planner `goal` check). Plan D2 renames the rendered
  tag `goal`→`workflow_goal`/`iteration_goal`. **Decision needed** (open_decisions): does the
  planner recipe emit `<workflow_goal>`/`<iteration_goal>` (per §5) or keep `<goal>`? Owned by
  the context-engine cluster; this check must match whatever it renders.
- line **274-275**: planner prior-iteration `"<task " in prompt` (relay evidence). `<task>`
  child element is **KEPT** per §4 ("child element stays `<task>`"; `<task>`→`<outcome>` is
  Tier-2 deferred). Leave as `<task `.
- line **281-289**: `role == "executor"` branch — `"<plan_spec>" in prompt` +
  `"<assigned_task" in prompt`. **`plan_spec` removed entirely (D3)** — drop the `plan_spec`
  check; keep `assigned_task`.
- line **290-298**: `role == "verifier"` branch — **DELETE** (verifier gone, WS3).
- line **299-308**: `role == "evaluator"` branch — rename to `"reducer"`; checks become
  `{"assigned_prompt": "<assigned_prompt>" in prompt, "needs": "<needs>" in prompt}` (per §5
  reducer recipe = `<needs>(outcomes) + <assigned_prompt>`); drop `<plan_spec>` and
  `<evaluation_criteria>` checks. line **303** `"<evaluation_criteria>"` is the cited anchor.
- line **239** comment "executor/verifier share the generator role" → update.

### 0b. `plan_shapes.py` — the reducer-block synthesis rule (CORE; pins the mechanical rule)

`minimal_full_plan(plan_spec, evaluation_criteria, task_id, task_spec, agent_name)` returns
`{plan_spec, evaluation_criteria, tasks:[{id,agent_name,deps:[]}], task_specs}`. Target shape:
- **drop `plan_spec`** key (D3); drop the `plan_spec` parameter — callers pass only narrative
  via `task_spec`. (M2: verify multi-task plans still pass with per-task `task_spec` only.)
- `tasks:[{id, agent_name, deps}]` → `tasks:[{id, agent_name, needs}]` (`deps`→`needs`).
- replace the `evaluation_criteria: list[str]` param with `reducers` synthesis. **THE RULE
  (pin this; it makes every caller mechanical):**
  ```
  reducers = [{
      "id": "reduce",
      "needs": [t["id"] for t in tasks],   # ALL generator local_ids ⇒ reachability holds trivially
      "prompt": "\n".join(evaluation_criteria) or "Confirm the plan tasks completed.",
  }]
  ```
  Keep a thin `evaluation_criteria`→`prompt` adapter param name OR rename the param to
  `criteria`; flag in open_decisions. `preflight_full_plan` / `preflight_defers_plan` then
  call through unchanged except for the param name.
- `preflight_defers_plan` keeps `plan["deferred_goal_for_next_iteration"] = …` unchanged.

This rule is `reducer.needs = [all generator ids]`, which satisfies §1 reachability for EVERY
existing scenario DAG (verified: diamond sink d, parallel sink d, serial sink c, mixed sink g,
blocked_descendants sink d, generator_failure sink d — but the all-ids rule is sink-agnostic
and always valid). No existing assertion pins a reducer-`needs` subset (verified — see §3).

---

## 1. WF-B disjoint owner groups (mechanical propagation only)

Partition principle: **scenario file + the test file(s) that assert it by `.name`/class live
in the SAME group.** Hub test files that import/assert MANY scenarios are assigned to a single
owner (Group H) so they never split a scenario across groups. Each group below shares NO
editing overlap with any other.

### Vocab transforms (apply within each owned file, mechanical):
- `tools.submission.evaluator import submit_evaluation_success/failure` →
  `tools.submission.reducer import submit_reduction_success/failure`
- `def evaluator_response(` → `def reducer_response(`  (the override; base hook renamed in §0)
- `"evaluation_criteria": [...]` plan-dict key → `"reducers": [{...}]` per §0b rule (for any
  INLINE plan dict not routed through `plan_shapes.py`)
- `"deps": [...]` → `"needs": [...]` in inline plan dicts
- `ctx.attempt.evaluation_criteria` → `ctx.attempt.reducers` (field owned by Attempt-DTO
  cluster; we CONSUME it — see open_decisions)
- `submit_evaluation_success/failure(...)` call args `{"summary":…, "passed_criteria":…/"failed_criteria":…}`
  → reducer arg shape (owned WS1/WS2 — see open_decisions; mechanical once pinned)
- string-match assertions `<evaluation_criteria>`→`<assigned_prompt>`/`<needs>`; `<dependency>`→`<needs>`
- `fail_reason="evaluator_failed"`/`"generator_failed"`/`"planner_failed"` → `"task_failed"`
- docstrings mentioning evaluator/verifier/EVALUATE stage/plan_spec
- file rename: `attempt_retry_evaluator_failure.py`→`attempt_retry_reducer_failure.py` (+ class
  `AttemptRetryEvaluatorFailure`→`AttemptRetryReducerFailure`, `.name` literal, all importers)

| Group | Owns (files) | Notes |
|---|---|---|
| **G1 — pipeline DAG scenarios** | `scenarios/pipeline/dependency_dag_diamond.py`, `dependency_dag_parallel.py`, `dependency_dag_serial.py`, `dependency_dag_mixed.py`, `dependency_blocked_descendants.py`, `generator_failure_quiescence.py` | All static inline plan dicts. `blocked_descendants` uses `failed_criteria` + `submit_evaluation_failure` (asserts attempt FAIL). Inline `deps`→`needs`, inline `evaluation_criteria`→`reducers`. |
| **G2 — pipeline lifecycle/retry scenarios** | `scenarios/pipeline/initial_workflow.py`, `iterative_deferral.py`, `attempt_budget_exhausted.py`, `attempt_retry_evaluator_failure.py`→rename, `attempt_retry_generator_failure.py`, `attempt_retry_planner_failure.py`, `pipeline/__init__.py` | `pipeline/__init__.py` imports the renamed class — keep in this group. `attempt_retry_*` use `plan_shapes.preflight_full_plan` (param rename only after §0b). |
| **G3 — pipeline nested/deferred + messages** | `scenarios/pipeline/nested_workflow.py`, `deferred_parent_planner_terminal_routing.py`, `initial_messages_capture.py` | `nested_workflow.py` has 2 classes (`NestedWorkflow` 83, `NestedWorkflowFailure` 126) + 3 inline `evaluation_criteria` plan dicts (25,55,73) + `recursive_handoff_goal` + `evaluator_response` at 113 (`passed_criteria`/118) and 156 (`failed_criteria`/161). `deferred_parent…` exercises handoff terminal routing. (`initial_workflow.py` is owned by G2 only — not duplicated here.) |
| **G4 — planner_validation scenarios** | `scenarios/planner_validation/*.py` (cycle_in_deps, defers_without_deferred_goal, duplicate_local_id, empty_tasks, unknown_agent_name, unknown_dep) + `planner_validation/__init__.py` | Rejection scenarios. `empty_tasks.py` is the `no_reducers` template (plan §10). `unknown_dep`/`cycle_in_deps` semantics use `deps` — rename to `needs`. NEW gate scenarios (`no_reducers`, reachability-reject) are CORE-authored, NOT here. |
| **G5 — top-level + correctness scenarios** | `scenarios/full_case_user_input.py`, `full_stack_adversarial.py`, `correctness_testing.py`, `user_input.py`, `lifecycle.py`, `scenarios/__init__.py` | `full_case`/`full_stack` rework verifier→executor+reducer (WS3). `scenarios/__init__.py` is the top registry — single owner here. |
| **G6 — sandbox scenarios** | `scenarios/sandbox/*.py` (auto_squash_commit_resume, background_shell, complex_project_build{,_grep_glob,_shell_edit_lsp}, ephemeral_workspace, heavy_io_zoned_concurrent, high_concurrency_layerstack_overlay_occ, occ_concurrent_conflicts, plugin) + `sandbox/__init__.py` | All carry `evaluation_criteria`/`deps` plan dicts + `evaluator_response`. Largest group by file count but each file is small/uniform. |
| **G7 — context-engine `<dependency>`→`<needs>` test assertions** | `tests/unit_test/test_task_center/test_context_engine/`: `test_renderer.py`, `test_tag_dictionary.py`, `test_context_outline.py`, `test_task_guidance.py`, `test_role_context_matches_diagram.py`, `test_recipes_other.py` | Pure string-match assertion updates: `<dependency>`→`<needs>`. **DRIFT:** these depend on the context-engine recipe cluster actually emitting `<needs>`; sequence AFTER that lands. `test_attempts.py`/`test_recipes_planner_closes_or_defers.py`/`test_attempts_hostile_body.py` assert evaluator/plan_spec behavior — those are CORE-adjacent (owned by context-engine cluster), NOT G7. |
| **G8 — mock asserting tests (tests/mock)** | `tests/mock/task_center/test_focused_scenarios.py`, `test_correctness.py`, `test_full_case_user_input.py`, `test_initial_messages_capture.py`, `test_deferred_parent_planner_terminal_routing.py`, `tests/mock/sandbox/full_stack/test_full_stack_adversarial.py`, `tests/mock/sandbox/project_build/test_project_build_shell_edit_lsp_three_parallel_agents.py`, `tests/mock/sandbox/capacity/test_capacity_scenario_packs.py`, `tests/mock/_focused_scenario_contracts.py`, `tests/mock/_project_build_contracts.py` | Assert scenario output: `evaluator`/`evaluation_criteria`/`task_summary`/`summaries`/`handoff` string-matches. `_focused_scenario_contracts.py`/`_project_build_contracts.py` are shared assertion helpers — single owner here. |
| **G9 — mock contract/import hub tests + capacity catalog** | `tests/mock/contracts/test_scenario_suite_imports.py`, `test_runner_imports.py`, `test_context_message_scenarios.py`, `test_scenario_loop_runner_planner_submit.py`, `test_correctness_via_event_source.py`, `tests/mock/contracts/test_scenario_event_source_spike.py`, `tests/mock/contracts/test_advisor_gate_negative_path.py`, `scenarios/capacity/pack_catalog.py`, `scenarios/capacity/__init__.py`, `tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py` | HUB tests importing MANY scenario classes — single owner avoids splitting. `test_scenario_loop_runner_planner_submit.py` has its OWN inline scenario (lines 32-55: `evaluation_criteria`,`deps`,`evaluator_response`,`submit_evaluation_success`) → apply the full scenario transform. `pack_catalog.py:138` `context.evaluator_iterative_deferral` registry name → check/rename. |

**Disjointness proof:** each scenario file appears in exactly one of G1-G6/G9 (capacity).
Each test file appears in exactly one of G7-G9. Hub tests (suite_imports, runner_imports,
context_message_scenarios) that reference scenarios across G1-G6 are confined to G9 and only
EDIT string literals there — they do not edit the scenario files. The scenario↔test coupling
(e.g. `test_focused_scenarios.py` asserts diamond+parallel+serial+…) is resolved by putting
ALL focused-scenario asserting tests in G8 and the scenario sources in G1-G6: G8 edits its own
assertion strings, G1-G6 edit the scenario sources; no file is in two groups.

---

## 2. Files explicitly NOT in WF-B (core-adjacent; owned by other clusters)

- `backend/tests/unit_test/test_task_center/test_lifecycle/**`, `test_domain/**`,
  `test_persistence/**`, `test_agent_launch/**`, `test_audit/**` — these assert the actual
  DTO/lifecycle behavior (`Attempt.evaluation_criteria`, `evaluator_task_id`, `ClosureReport`,
  `final_outcome`, `EVALUATE` stage, `submit_execution_handoff`, `task_summary`) being rewritten
  by WS1/WS2/WS4/WS6/WS7. They are NOT mechanical vocab; they change WITH the core DTO edits and
  are owned by those clusters. Listed here so WF-B agents do NOT touch them.
- `test_domain/test_ancestry.py`, `test_domain/test_iteration_closure_report.py` —
  ancestry/closure removed (D9/D10); these tests are deleted/rewritten by WS6/WS7.
- `tests/unit_test/test_task_center/test_context_engine/test_attempts*.py`,
  `test_recipes_*.py`, `test_engine.py`, `test_scope.py`, `test_packet.py` — assert
  evaluator recipe / scope / plan_spec behavior; owned by the context-engine cluster.

---

## 3. DRIFT (plan claims vs current code — verified)

- `scenario_loop_runner.py`: plan WS9 cites `:303` (`<evaluation_criteria>`) ✓ (line 303),
  but `<task `/`<assigned_task` are at **274-275 / 285,294,302**, not `:274,302`. `<plan_spec>`
  checks at 283, 291, 300. Also a `<goal>` check at **250, 258** the plan did not list (D2 may
  require `<workflow_goal>`/`<iteration_goal>`).
- `scenario_adapter.py`: plan implies handoff rename in scenarios; in reality
  `submit_execution_handoff` appears ONLY in `scenario_adapter.py` (×2, lines 203, 205) within
  the cluster's source — scenario files use `recursive_handoff_goal` text, not the terminal.
- `attempt_retry_evaluator_failure.py` docstring (lines 1-10) states
  `fail_reason="evaluator_failed"` — stale vs `TASK_FAILED` collapse (§4 fail-reason row).
- conftest.py (`test_task_center`) registers a `verifier` agent (lines 190-199) AND an
  `evaluator` agent — both legacy; the plan's WS9 list did not call this shared fixture out
  explicitly, but it gates every lifecycle test and is hand-edited CORE.
- Plan §3 lists `tools/submission/reducer/ (← evaluator/)` — the import path scenarios use is
  `tools.submission.evaluator`; the rename to `tools.submission.reducer` is owned by WS1 (tools
  cluster). WF-B scenario edits must land AFTER that module exists, else ImportError.
- `scenarios/__init__.py`, `pipeline/__init__.py`, `planner_validation/__init__.py` each import
  scenario classes by name — the `attempt_retry_evaluator_failure`→`reducer` file/class rename
  must update its `__init__.py` in the SAME group (G2 owns it).

---

## 4. open_decisions (ambiguities the plan does not pin)

1. **Reducer terminal arg schema** (`submit_reduction_success/failure`): scenarios currently
   pass `{"summary", "passed_criteria"/"failed_criteria"}`. The new arg shape
   (`status`/`outcomes`/`terminal_tool_result` per §2 `ReducerSubmission`) is owned by WS1/WS2
   and NOT pinned in the plan. WF-B `reducer_response` builders CANNOT be written until WS1/WS2
   pin it. **Source of truth: WS1/WS2.**
2. **`plan_shapes.py` param name** after dropping `evaluation_criteria`: rename param to
   `criteria` vs keep `evaluation_criteria` as a prompt-source alias. I propose `criteria`
   (cleaner) but flag for the core lane.
3. **Planner rendered goal tag**: `<goal>` vs `<workflow_goal>`/`<iteration_goal>` (D2). The
   `_inspect_prompt` planner checks (lines 250, 258) must match the context-engine recipe.
   Owned by context-engine cluster; cluster-6 `_inspect_prompt` follows it.
4. **`pack_catalog.py:138`** `CapacityPackSpec("context.evaluator_iterative_deferral",
   test_path=…test_recipes_other.py)` — VERIFIED: this is a context-engine *pack registry
   name* string (not a mock scenario class), pointing at the context-engine cluster's test.
   Rename to `context.reducer_iterative_deferral` ONLY in lockstep with the context-engine
   recipe/pack rename; cross-cluster coordination. Owned-by-G9 edit but gated on context-engine.
5. **NEW gate scenarios** (`no_reducers` reject, reachability reject, multi-reducer partial-fail,
   BLOCKED-generator → FAIL) from plan §10 — these are CORE-authored (new files, new DAG logic),
   NOT WF-B mechanical. Decide which cluster authors them (likely WS2 core).
