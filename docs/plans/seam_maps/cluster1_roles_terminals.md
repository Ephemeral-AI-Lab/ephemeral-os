# Cluster 1 — WS1 (reducer role replaces evaluator) + WS3 (remove verifier profile)

Edit manifest for the roles/terminals/profiles cluster. Verified against current
code on `main` (commit `fabce1b70`). Line numbers below are CURRENT (re-verified),
not the plan's. Where the plan's anchors drifted, it is called out in **DRIFT**.

Scope boundary: this cluster owns the EVALUATOR→REDUCER role rename, the
`evaluator/*`→`reducer/*` terminal-tool package, the verifier-profile deletion, and
`submit_execution_handoff`→`submit_workflow_handoff` rename. Several files in this
cluster are SHARED with WS2/WS4/WS6/WS7 (`submissions.py`, `orchestrator.py`,
`stage_advancer.py`, `attempt/state.py`, `_core/persistence.py`, db models/stores).
For those I describe ONLY the role/terminal-vocab slice this cluster is responsible
for and flag the co-edit in **Coordination**. The deep logic edits in those files
(two-tuple gate, outcomes algebra, closure removal) belong to the other clusters.

---

## A. CORE FILES (hand-edited logic)

### A1. `backend/src/agents/definition/model.py` — `AgentRole.EVALUATOR`→`REDUCER`
- CURRENT: `class AgentRole(StrEnum)` at L25; member `EVALUATOR = "evaluator"` at **L38**
  (plan said `:38` — correct). Members: PLANNER/GENERATOR/EVALUATOR/HELPER/SUBAGENT.
- Docstring L25-34 says `GENERATOR covers both the executor and verifier profiles
  (distinguished by name)`. After WS3 there is no verifier profile — **update the
  docstring** to drop the "and verifier" clause; GENERATOR is executor-only.
- L65-69 field comment lists `planner / generator / evaluator / helper / subagent`;
  change `evaluator`→`reducer`.
- TARGET: `REDUCER = "reducer"`; docstring + field-comment vocab updated.
- RISK: enum VALUE changes `"evaluator"`→`"reducer"`. Per MEMORY (`db_engine_no_enum_value_migration_hook`)
  there is NO enum-value migration hook; durable rows live only in disposable
  `task_center_runner/*.db` scratch, so no migration code needed. The string value flows
  into `metadata["role"]` audit tags and the PRIMARY_ROLES set (A11) — keep them in sync.

### A2. `backend/src/agents/definition/loader.py` — role-validation error text
- CURRENT: the `role` hard-fail message at **L65-70** lists `planner / generator /
  evaluator / helper / subagent` (plan said `:65-69` — off by one; it's L65-70).
- TARGET: `evaluator`→`reducer` in the error string. No logic change.
- TEST that pins this literal: `test_role_context_matches_diagram.py` (see P-list).

### A3. `backend/src/task_center/_core/task_state.py` — `TaskCenterTaskRole` + `SpawnReason` removal
- CURRENT: `TaskCenterTaskRole` L13-16: PLANNER/GENERATOR/**EVALUATOR = "evaluator"** (L16,
  plan said `:16` — correct). `SpawnReason` StrEnum L19-24 with ATTEMPT_PLANNER /
  ATTEMPT_GENERATOR / **ATTEMPT_EVALUATOR**. `TaskCenterTaskStatus` L27-33 unchanged by
  this cluster. `TERMINAL_GENERATOR_STATUSES` L36-42 unchanged.
- TARGET: `EVALUATOR = "evaluator"` → `REDUCER = "reducer"`. **Delete the entire
  `SpawnReason` class** (D5). Status enum + terminal-status frozenset untouched.
- **Coordination (SpawnReason removal fans out to non-cluster files):** `spawn_reason`
  is referenced in `attempt/orchestrator.py:45,107,287`, `attempt/stage_advancer.py:33,236`,
  `_core/persistence.py:186`, `db/stores/task_center_store.py:57,140,158,173`,
  `db/models/task_center.py:93-95`, `task_center_runner/audit/recorder.py:170`. Those are
  WS6 (store-signature) + WS2 (orchestrator/stage) territory. This cluster removes the ENUM;
  the importers must drop the kwarg in the same landing or imports break. Flag to WS2/WS6.

### A4. `backend/src/task_center/_core/primitives.py` — `evaluator_task_id`→`reducer_task_id`
- CURRENT: `evaluator_task_id(attempt_id)` L32-33 returns `f"{attempt_id}:evaluator"`.
  `generator_task_id(attempt_id, local_task_id)` L28-29 returns `f"{attempt_id}:gen:{local}"`.
  `__all__` L50-56 lists `evaluator_task_id`.
- TARGET: replace with `def reducer_task_id(attempt_id: str, local_task_id: str) -> str:
  return f"{attempt_id}:red:{local_task_id}"`. NOTE the **signature change** — `reducer_task_id`
  takes `(attempt_id, local_id)` (mirrors `generator_task_id`), whereas the old
  `evaluator_task_id` took only `attempt_id` (one evaluator per attempt). This is because
  reducers are now ≥1 plan tasks with local_ids. Update `__all__`.
- RISK: every old `evaluator_task_id(attempt.id)` call site (single-arg) must become
  `reducer_task_id(attempt.id, local_id)` (two-arg). Call sites are WS2 territory
  (`stage_advancer.py`, `orchestrator.py`, `attempt/state.py`). The id PREFIX changes
  `:evaluator`→`:red:<local>` — the `terminal_routing.py` `is_nested` predicate parses
  attempt-id prefixes, so confirm no code splits on `":evaluator"` literally.

### A5. `tools/submission/evaluator/` → `tools/submission/reducer/` — rename package + tools
- CURRENT package (verified): `evaluator/__init__.py`,
  `evaluator/submit_evaluation_success/{__init__.py, submit_evaluation_success.py, prompt.py}`,
  `evaluator/submit_evaluation_failure/{__init__.py, submit_evaluation_failure.py, prompt.py}`.
- TARGET (per §1, D1): directory `reducer/`; tools `submit_reduction_success` /
  `submit_reduction_failure`. Each leaf tool:
  - `submit_reduction_success/submit_reduction_success.py`: tool `name="submit_reduction_success"`,
    `input_model=SubmitReductionSuccessInput`. Current input is `{summary, passed_criteria}`
    (L28-30). **OPEN DECISION OD1** — the new ReducerSubmission DTO uses `status`+`outcomes`
    (WS4 vocab). Proposed input: `summary: str (min_length=1)` kept as the reduction text;
    `passed_criteria`/`failed_criteria` payload lists are evaluator-criteria-shaped and the
    plan removes `evaluation_criteria` (WS2) — recommend DROPPING the criteria list arg, keeping
    only `summary`. Calls `orchestrator.apply_reducer_submission(ReducerSubmission(...,
    status="success", ...))` (was `apply_evaluator_submission(EvaluatorSubmission(..., outcome=...))`).
  - `submit_reduction_failure/...`: mirror with `status="failure"`.
  - `prompt.py` description factories: rewrite "evaluator run" → "reducer run", drop
    `<evaluation_criteria>` references; `_names.py` constant import
    `SUBMIT_EVALUATION_FAILURE_TOOL_NAME`→`SUBMIT_REDUCTION_FAILURE_TOOL_NAME` (A9).
  - `__init__.py` shims: the `sys.modules[__name__] = _impl` re-export pattern is preserved;
    just rename the impl module + the import.
  - metadata `submission_kind`: `"evaluator_success"/"evaluator_failure"` →
    `"reduction_success"/"reduction_failure"` (OD2 — naming unpinned; propose these).
- RISK: the `submit_reduction_*` payload shape now feeds `outcomes`+`terminal_tool_result`
  (WS4). This cluster lands the rename + `apply_reducer_submission` call; WS4 owns whether
  the DTO field is `summary`/`text`/`outcomes`. Keep `summary=` until WS4 renames it, or
  co-land. See Coordination C1.

### A6. `tools/submission/verifier/` — DELETE (WS3)
- CURRENT: `verifier/__init__.py` + `submit_verification_success/*` + `submit_verification_failure/*`.
  Each calls `apply_generator_submission(GeneratorSubmission(..., payload={"generator_role":
  "verifier", ...}))`.
- TARGET: delete the whole `verifier/` directory.
- RISK: removing the package breaks `_factory.py` import (A7) and `_names.py` constants (A9)
  and the registry descriptors (A10) — all in this cluster, land together.

### A7. `backend/src/tools/submission/_factory.py` — drop verifier + evaluator→reduction tools
- CURRENT (verified, NOT plan's `:16-35`): L7-10 import
  `submit_evaluation_failure, submit_evaluation_success` from `.evaluator`; **L16-19** import
  `submit_verification_failure, submit_verification_success` from `.verifier`; `make_submission_tools`
  L27-40 lists all 11 tools incl. `submit_verification_*` (L34-35) and `submit_evaluation_*` (L36-37).
- TARGET: delete the `.verifier` import block + the two list entries; rename the `.evaluator`
  import to `.reducer` importing `submit_reduction_failure, submit_reduction_success`; update
  list entries to the reduction tools.

### A8. `tools/submission/executor/submit_execution_handoff/` → `submit_workflow_handoff/` (FLAG-5)
- CURRENT: tool `name="submit_execution_handoff"` (L59), input `SubmitExecutionHandoffInput`
  (L39), `get_submit_execution_handoff_description()`, pre-hooks reference the string name
  (L66-67), metadata `submission_kind="workflow_start"` (L94). `executor/__init__.py` L4-5,9
  imports/exports it.
- TARGET: rename dir to `submit_workflow_handoff/`, tool `name="submit_workflow_handoff"`,
  input `SubmitWorkflowHandoffInput`, description factory + pre-hook strings updated.
  Keep `goal_handoff` arg name (the plan renames the TERMINAL, not the arg; arg→workflow
  rename is Tier-2). `executor/__init__.py` import + `__all__` updated.
- **Coordination C2 (WS7/WS8):** `start_delegated_workflow` in `context/executor.py:71-80`
  currently calls `WorkflowStarter.start(prompt=..., origin=WorkflowOrigin.task(...))`. WS7/WS8
  rewrite that to `parent_task_id=`. This cluster only renames the TERMINAL TOOL + its package;
  do NOT touch the `WorkflowOrigin` call (that's WS7). If WS7 lands first, the handoff tool body
  is unchanged except the package/name rename.

### A9. `backend/src/tools/_names.py` — terminal-name constants
- CURRENT: `SUBMIT_EXECUTION_HANDOFF_TOOL_NAME` L33; `SUBMIT_VERIFICATION_SUCCESS/FAILURE` L36-37;
  `SUBMIT_EVALUATION_SUCCESS/FAILURE` L40-41; `__all__` L69-73 mirrors. (Plan did not enumerate
  this file — DRIFT: it MUST be edited or `prompt.py` imports break.)
- TARGET: `SUBMIT_EXECUTION_HANDOFF_TOOL_NAME = "submit_execution_handoff"` →
  `SUBMIT_WORKFLOW_HANDOFF_TOOL_NAME = "submit_workflow_handoff"`; DELETE the two
  `SUBMIT_VERIFICATION_*` constants; rename `SUBMIT_EVALUATION_SUCCESS/FAILURE_TOOL_NAME`
  → `SUBMIT_REDUCTION_SUCCESS/FAILURE_TOOL_NAME` (values `submit_reduction_*`). Update `__all__`.
- Consumer of `SUBMIT_EXECUTION_HANDOFF`: none in src prompts (only `executor` handoff prompt
  imports `SUBMIT_EXECUTION_SUCCESS/BLOCKER`, not handoff). `SUBMIT_EVALUATION_FAILURE` is
  imported by `evaluator/submit_evaluation_success/prompt.py:5` → repoint to reduction.

### A10. `backend/src/tools/_terminals/registry.py` — `TERMINAL_DESCRIPTORS`
- CURRENT (verified, NOT plan's `:112-140`/`:141-169`): descriptors are
  `submit_evaluation_success` **L112-126**, `submit_evaluation_failure` **L127-140**,
  `submit_verification_success` **L141-154**, `submit_verification_failure` **L155-170**.
- TARGET: DELETE the two `submit_verification_*` descriptors (WS3); rename the two
  `submit_evaluation_*` descriptors → `submit_reduction_success`/`submit_reduction_failure`
  and rewrite their `selection_guidance`/`advisor_review_focus` prose to drop
  `<evaluation_criteria>` (WS2 removes it) and speak in reducer/`<assigned_prompt>` terms.
  The `submit_execution_handoff` descriptor **L64-76** → key + name `submit_workflow_handoff`.
- The `submit_execution_success` descriptor L44-50 mentions `<dependency>` outputs — WS10
  renames the wrapper tag to `<needs>`; leave that to WS10 unless co-landing (note only).
- TEST: `test_descriptor_registry.py` is a static completeness test (profile MD terminals ⊆
  registry keys) — it will fail if profile terminals and registry drift. Must land together.

### A11. `backend/src/task_center/attempt/launch.py` — agent-name const, factory, fail-reasons, exhaustion
- CURRENT (verified): imports `AttemptFailReason, AttemptStatus` from `attempt.state` (L17);
  imports `EvaluatorSubmission, GeneratorSubmission, PlannerFailureSubmission` from
  `task_center.submissions` (L24-28). `_ROLE_FAIL_REASONS` dict **L197-201** maps
  PLANNER→PLANNER_FAILED, GENERATOR→GENERATOR_FAILED, EVALUATOR→EVALUATOR_FAILED.
  `_report_exhaustion` L254-296 branches on role; the EVALUATOR branch L285-294 builds
  `EvaluatorSubmission(... outcome="failure" ...)` and calls `apply_evaluator_submission`.
  `EVALUATOR_AGENT_NAME = "evaluator"` **L303** (plan said `:303` — correct).
  `for_evaluator(*, attempt, task_id)` **L354-369** builds role=EVALUATOR, base_agent_name=
  EVALUATOR_AGENT_NAME, scope=`ContextScope.for_evaluator(...)`, needs=
  `tuple(attempt.generator_task_ids)`.
- TARGET (this cluster's vocab slice):
  - L17 import: `AttemptFailReason` still exists but its members collapse to `TASK_FAILED |
    STARTUP_FAILED` (WS2 owns the enum edit in `attempt/state.py`/`_core/state.py`). This
    cluster: rewrite `_ROLE_FAIL_REASONS` (L197-201) — since all roles now map to
    `AttemptFailReason.TASK_FAILED`, the per-role dict can collapse to a single constant.
    Recommend deleting the dict and using `AttemptFailReason.TASK_FAILED` directly at
    L226 (`_fail_unowned_attempt`). **OD3** — keep dict-shaped for symmetry vs inline constant;
    propose inline constant (smaller).
  - Import `EvaluatorSubmission`→`ReducerSubmission`; exhaustion EVALUATOR branch (L285-294)
    → REDUCER branch calling `apply_reducer_submission(ReducerSubmission(..., status="failure",
    ...))`. `TaskCenterTaskRole.EVALUATOR`→`REDUCER` at L200 and L285.
  - `EVALUATOR_AGENT_NAME = "evaluator"` → `REDUCER_AGENT_NAME = "reducer"` (L303).
  - `for_evaluator`→`for_reducer` (L354-369): role=`TaskCenterTaskRole.REDUCER`,
    base_agent_name=`REDUCER_AGENT_NAME`, scope=`ContextScope.for_reducer(...)`. **DRIFT vs plan**:
    plan §WS1 says `for_reducer` "now takes `task_id`" — current `for_evaluator` ALREADY takes
    `task_id` as a param. The plan's "now takes task_id" likely means the SCOPE
    (`ContextScope.for_reducer`) gains `task_id` (WS1 line: "`ContextScope.for_evaluator`→
    `for_reducer` (now takes `task_id`)"). See A12. Also `needs=tuple(attempt.generator_task_ids)`
    stays in the WS1 slice but WS2 changes how reducer tasks are created (≥1, per-local-id);
    `for_reducer` may need the reducer's own local_id. Flag to WS2.
  - L109 comment "planner/generator/evaluator launches" → "planner/generator/reducer".
  - **Coordination:** L113 passes `task_center_attempt_id=launch.attempt_id` into
    ExecutionMetadata — WS6/D5 removes `task_center_attempt_id`; that is WS6's edit, not this
    cluster's. Do not remove it here.
- TEST: `test_agent_launch_factory_for_role.py` references `EVALUATOR_AGENT_NAME`/`for_evaluator`.

### A12. `backend/src/task_center/context_engine/scope.py` — `for_evaluator`→`for_reducer`
- CURRENT: `for_evaluator(*, workflow_id, iteration_id, attempt_id)` **L88-101** (returns scope
  WITHOUT task_id). `for_generator` L71-86 takes `task_id`.
- TARGET: rename to `for_reducer`; **add `task_id: str`** param (per WS1 "now takes `task_id`")
  so the reducer recipe can resolve its own task — matching `for_generator`'s shape. The reducer
  recipe (WS4/§5 `recipes/reducer.py`) needs the task's `<assigned_prompt>`, keyed by task_id.
- **Coordination:** the recipe itself (`recipes/evaluator.py`→`reducer.py`) is WS4/§5; this
  cluster only renames the scope factory. If `for_reducer` requires `task_id` but the recipe
  isn't updated to consume it yet, the recipe still works (extra scope field is harmless).

### A13. `backend/src/task_center/submissions.py` — `EvaluatorSubmission`→`ReducerSubmission`
- CURRENT: `EvaluatorSubmission` L60-68: `{attempt_id, task_id, outcome:Literal["success",
  "failure"], summary, payload}`. `GeneratorSubmission` L49-57 unchanged by this cluster.
- TARGET (this cluster's slice — the RENAME): `class ReducerSubmission` with
  `status: Literal["success","failure"]` (was `outcome`). Per §2 the full target is
  `{attempt_id, task_id, status, outcomes[], terminal_tool_result}` — but the `outcomes`/
  `terminal_tool_result` fields are WS4. **Coordination C1:** land the class RENAME +
  `outcome`→`status` here; let WS4 swap `summary`→`outcomes`+`terminal_tool_result`. Re-export
  in `task_center/__init__.py` (the facade currently exports `EvaluatorSubmission`).
- `task_center/__init__.py` re-export: rename `EvaluatorSubmission`→`ReducerSubmission`
  (importers: `attempt/launch.py`, `orchestrator.py`, `orchestrator_registry.py`, the
  reducer tools).

### A14. `backend/src/task_center/attempt/orchestrator.py` — `apply_evaluator_submission`→`apply_reducer_submission`
- CURRENT (plan said `:161-164,307-325`): imports `SpawnReason` (L45), uses
  `SpawnReason.ATTEMPT_PLANNER` (L107), `ATTEMPT_GENERATOR` (L287); has
  `apply_evaluator_submission`; uses `evaluator_task_id`.
- TARGET (this cluster's vocab slice): rename `apply_evaluator_submission`→
  `apply_reducer_submission`; param `EvaluatorSubmission`→`ReducerSubmission`; drop
  `SpawnReason` import + usages (per A3); `evaluator_task_id`→`reducer_task_id` (per A4).
- **Coordination:** the BODY logic (gate, two-tuple, stage collapse, outcomes write) is WS2/WS4.
  This cluster supplies the renamed method signature + the `SpawnReason`/`evaluator_task_id`
  vocab the body uses. Co-land with WS2.

### A15. `backend/src/task_center/attempt/orchestrator_registry.py` — protocol method rename
- CURRENT: declares `apply_evaluator_submission` on `RegisteredAttemptOrchestrator` protocol
  + imports `EvaluatorSubmission`.
- TARGET: `apply_reducer_submission` + `ReducerSubmission`. Mechanical but it's a protocol
  signature (CORE — keep in lockstep with A11 caller + A14 impl).

### A16. `backend/src/agents/profile/main/evaluator.md` → `reducer.md` (rewrite)
- CURRENT: frontmatter `name: evaluator`, `role: evaluator`, `terminals:
  [submit_evaluation_success, submit_evaluation_failure]`, `context_recipe: evaluator`,
  `skill: ../../../../config/skills/evaluator/SKILL.md`. Body speaks of `<plan_spec>`,
  `<evaluation_criteria>`, per-task `<task>` summaries, inline-edit policy, terminal list.
- TARGET: rename file to `reducer.md`; `name: reducer`, `role: reducer`,
  `terminals: [submit_reduction_success, submit_reduction_failure]`,
  `context_recipe: reducer`. Body rewrite to reducer semantics: digests/gates its
  `<needs>` outcomes against its `<assigned_prompt>` (drop `<plan_spec>`/`<evaluation_criteria>`).
  Keep the inline-edit policy + Submission discipline (they're role-agnostic) but retarget
  terminal names. **OD4 — skill path:** the `config/skills/evaluator/SKILL.md` dir — rename to
  `config/skills/reducer/`? Propose YES (mirror profile rename); this is OUTSIDE my read set
  (config/skills) — flag to WS9/docs. If the skill dir is not renamed, point `skill:` at the
  existing `evaluator/SKILL.md` to avoid a missing-file loader hard-fail.
- The `_main_role_contract.md` (prepended to all main profiles) was read indirectly — it is
  role-agnostic; no change needed unless it names "evaluator" (verify on edit).

### A17. `backend/src/agents/profile/main/generator_verifier.md` — DELETE (WS3)
- CURRENT: `name: verifier`, `role: generator`, terminals `submit_verification_*`,
  `context_recipe: generator`, NO terminal_routing, NO skill.
- TARGET: delete the file.

### A18. `backend/src/agents/profile/main/executor.md` — handoff terminal rename + verifier-mention scrub
- CURRENT: terminals include `submit_execution_handoff` (L26); body L37,39,52 reference
  `submit_execution_handoff`; L51 says success "closes this generator task with a passing
  outcome that the attempt's **evaluator** reads."
- TARGET: `submit_execution_handoff`→`submit_workflow_handoff` (frontmatter L26 + body L37,39,52);
  L51 "evaluator reads"→"reducer reads". No structural change.
- `executor_routing.py` (A19) terminal frozenset literals must match.

### A19. `backend/src/agents/profile/main/executor_routing.py` — handoff string in frozenset
- CURRENT: `select_terminals` returns frozensets containing `"submit_execution_handoff"`
  (L16,19). Docstring L5 references `task_center/_core/terminal_tool_routing.py`.
- TARGET: `"submit_execution_handoff"`→`"submit_workflow_handoff"` (L16,19). Docstring path
  `terminal_tool_routing.py`→`terminal_routing.py` is WS10 (note only, or co-land).

### A20. `backend/src/task_center_runner/core/bootstrap.py` — `_REQUIRED_AGENT_NAMES`
- CURRENT: `_REQUIRED_AGENT_NAMES = frozenset({"planner", "executor", "verifier", "evaluator"})`
  **L28** (plan said `:28` — correct). L25-27 comment names the four profile files incl.
  `generator_verifier.md (name=verifier)` and `evaluator.md`.
- TARGET: `frozenset({"planner", "executor", "reducer"})` — drop `verifier` (WS3) AND
  `evaluator` (renamed), add `reducer`. Update the L24-27 comment (drop verifier, evaluator→reducer).

### A21. `backend/src/task_center_runner/audit/recorder.py` — role sets + verifier display-role
- CURRENT (this IS the plan's `audit/recorder.py:87,94` — under task_center_runner, DRIFT
  from "task_center/_core/audit.py"): `PRIMARY_ROLES` **L86-88** = `{planner, executor,
  verifier, evaluator}`; `_ATTEMPT_CHILD_ROLES` **L93-95** = `{planner, executor, verifier,
  evaluator, generator}`; `_display_role` **L688-695** maps `role=="generator" and
  agent_name in {executor, verifier}` → agent_name.
- TARGET: PRIMARY_ROLES → `{planner, executor, reducer}`; _ATTEMPT_CHILD_ROLES →
  `{planner, executor, reducer, generator}`; `_display_role` drop `"verifier"` from the
  agent_name set (now `{executor}`); display "reducer" via role (reducer is its own role now,
  not a generator-by-name). NOTE: reducer role string flows through `_display_role` as
  `str(target.role)` = "reducer" automatically.
- **Coordination (WS4/WS7 fields):** `_serialize_workflow` L102-115 reads `origin_kind`,
  `requested_by_task_id`, `final_outcome` (WS7 removes); `_serialize_iteration` L118-134 reads
  `plan_spec`, `task_summary` (WS4); `_serialize_attempt` L137-154 reads `plan_spec`,
  `evaluation_criteria`, `evaluator_task_id` (WS2); `_serialize_task` L157-173 reads `summaries`,
  `task_center_attempt_id`, `fix_target_id`, `spawn_reason` (WS4/WS6). Those serializers are NOT
  this cluster's edit — but the role-set + display edits here MUST co-land so the audit tree
  shows `reducer` dirs. Flag the serializer co-edits to WS4/WS6/WS7.

### A22. `backend/src/task_center_runner/audit/node_id.py` — `PrimaryRole` Literal
- CURRENT: `PrimaryRole = Literal["planner", "executor", "verifier", "evaluator"]` **L12-17**
  (plan said `:15` — it's L12-17). Used as `NodeId.agent_role` type.
- TARGET: `Literal["planner", "executor", "reducer"]` (drop verifier, evaluator→reducer).

### A23. `backend/src/task_center/context_engine/agent_directives.py` — directive table
- CURRENT: `AGENT_DIRECTIVES` L17-23: keys `planner, executor, verifier, evaluator, explorer`;
  `"evaluator": "Verify the current attempt against <evaluation_criteria>."` (L21);
  `"verifier": "Complete <assigned_task>."` (L20).
- TARGET: drop the `"verifier"` key (WS3, plan said `:20`); rename `"evaluator"` key →
  `"reducer"` with directive `"Reduce/judge the current attempt against <assigned_prompt>."`
  (OD5 — exact directive wording unpinned; propose this, mirroring planner/executor terseness).
- TEST: `test_agent_directives.py` pins these strings.

### A24. `backend/src/task_center/agent_launch/task_guidance_dispatch.py` — `_AGENTS_WITH_TASK_GUIDANCE`
- CURRENT: `_AGENTS_WITH_TASK_GUIDANCE` L26-36 = `{planner, executor, verifier, evaluator}`
  (plan said `:26-36` — correct); L29-32 comment explains `generator_verifier.md` registers
  as `verifier`.
- TARGET: `{planner, executor, reducer}` — drop verifier (WS3), evaluator→reducer; delete the
  L29-32 verifier comment.

---

## B. PROPAGATION FILES (mechanical vocab / string-match only)

These are tests + mock contracts that string-match the renamed vocab. No logic. Grep-verified
they reference this cluster's renamed symbols. (Mock scenario rewrites for verifier→reducer
gates are WS9, not enumerated here unless they string-match a role/terminal name.)

- `backend/tests/unit_test/test_agents/test_verifier_evaluator_edit_tools.py` — has
  `test_verifier_*` (DELETE — verifier gone) + `test_evaluator_*` (`_load_named("evaluator")`
  → `"reducer"`; file should be renamed `test_reducer_edit_tools.py`). CORE-adjacent (asserts
  policy) but the change is mechanical name-swap + dropping the verifier test.
- `backend/tests/unit_test/test_agents/test_routing_acceptance.py` — verifier/handoff refs.
- `backend/tests/unit_test/test_agents/test_agent_markdown.py` — `submit_execution_handoff` +
  verifier/evaluator profile assertions.
- `backend/tests/unit_test/test_agents/test_profile_routing.py` — `submit_execution_handoff` refs.
- `backend/tests/unit_test/test_agents/test_planner_profile_md.py` — verifier refs.
- `backend/tests/unit_test/test_agents/test_helper_profile_identity_sentences.py` — verifier refs.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_role_context_matches_diagram.py`
  — pins loader role-list literal `planner / generator / evaluator` (A2) + `<dependency>` (WS10).
- `backend/tests/unit_test/test_task_center/test_context_engine/test_agent_directives.py` — A23 table.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_task_guidance.py` — evaluator refs.
- `backend/tests/unit_test/test_task_center/test_agent_launch/test_terminal_tool_router.py` — evaluator refs.
- `backend/tests/unit_test/test_task_center/test_lifecycle/test_agent_launch_factory_for_role.py`
  — `EVALUATOR_AGENT_NAME`, `for_evaluator`, `_ROLE_FAIL_REASONS` (A4,A11). Semi-core (asserts
  factory shape) — update to `REDUCER_AGENT_NAME`/`for_reducer`/`reducer_task_id` two-arg.
- `backend/tests/unit_test/test_tools/conftest.py` — verifier/evaluator/handoff tool fixtures.
- `backend/tests/unit_test/test_tools/test_submission_tool_registration.py` — registers all
  submission tools (the 11) incl. verification/evaluation/handoff names.
- `backend/tests/unit_test/test_tools/test_submission_terminal_routing.py` — verifier/evaluator/handoff.
- `backend/tests/unit_test/test_tools/test_submission_soft_reminders.py` — `submit_execution_handoff`.
- `backend/tests/unit_test/test_tools/test_ask_advisor_retry.py` — `submit_execution_handoff`.
- `backend/tests/unit_test/test_tools/test_schema_summary.py` — evaluation tool schemas.
- `backend/tests/unit_test/test_tools/test_terminals/test_descriptor_registry.py` — static
  completeness (A10); must reflect deleted verifier + renamed reduction/handoff descriptors.
- `backend/tests/unit_test/test_tools/test_hooks/test_iws_gate_wiring.py`,
  `test_require_no_inflight_background_tasks.py`,
  `test_submission/test_advisor_approval_prehook.py` — pre-hook name lists incl. these terminals.
- `backend/tests/unit_test/test_task_center/conftest.py` — verifier/evaluator/handoff fixtures.
- `backend/tests/contracts/test_tool_intent_drift.py` — intent map over all tool names.
- `backend/src/task_center_runner/tests/mock/_project_build_contracts.py`,
  `_focused_scenario_contracts.py`, `contracts/test_advisor_gate_wiring.py`,
  `agent/mock/scenario_adapter.py` — string-match `submit_execution_handoff` → workflow_handoff.
- `backend/tests/unit_test/test_benchmarks/test_sweevo_mock_agent_execution.py`,
  `test_sweevo_audit_recorder.py`, `test_sweevo_snapshot_verifier.py` — role/terminal name strings.

(NOTE: `notification_triggers/request_workflow_after_edit.py` references
`submit_execution_handoff` as a trigger target string — it's SRC, treat as core string-match;
verify the trigger wiring still resolves after rename.)

---

## C. Coordination summary (shared files — who owns what)
- **C1 `submissions.py` / reducer tool bodies:** this cluster renames `EvaluatorSubmission`→
  `ReducerSubmission` + `outcome`→`status`; WS4 swaps `summary`→`outcomes`+`terminal_tool_result`.
- **C2 `context/executor.py`:** WS7/WS8 rewrites `WorkflowOrigin`→`parent_task_id`; this cluster
  does NOT touch that file (the handoff tool rename is package-level only).
- **C3 `orchestrator.py`/`orchestrator_registry.py`/`stage_advancer.py`:** this cluster supplies
  the `apply_reducer_submission` rename + `SpawnReason`/`evaluator_task_id` removal vocab; WS2
  owns the gate/two-tuple/stage logic. Co-land.
- **C4 `attempt/state.py` (`AttemptFailReason`, `EVALUATOR_FAILED`):** WS2 collapses the enum to
  `TASK_FAILED|STARTUP_FAILED`; this cluster's `launch.py` `_ROLE_FAIL_REASONS` rewrite depends
  on that enum edit.
- **C5 audit serializers / db models / stores:** WS4/WS6/WS7 own field removals; this cluster
  owns only the role-string sets (A21,A22) and the `SpawnReason` enum deletion (A3) whose kwarg
  drop fans into stores/models.
