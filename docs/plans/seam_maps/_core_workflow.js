export const meta = {
  name: 'reducers-redesign-core',
  description: 'Coherence-bound core of the reducers+outcomes redesign: 7 dependency-ordered steps, implement→verify, halt-on-failure',
  phases: [
    { title: 'Step1-reducer-foundation' },
    { title: 'Step2-state-consolidation' },
    { title: 'Step3-outcome-type-root' },
    { title: 'Step4-db-stores-protocols' },
    { title: 'Step5-gate-submit' },
    { title: 'Step6-recipes-prompts' },
    { title: 'Step7-closure-handoff-root' },
  ],
}

const PLAN = 'docs/plans/reducers_outcomes_redesign_PLAN.md'
const DECISIONS = 'docs/plans/seam_maps/_DECISIONS.md'

const IMPLEMENT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['step', 'status', 'files_changed', 'files_deleted', 'emergent_decisions', 'deviations', 'self_check', 'notes_for_next_step'],
  properties: {
    step: { type: 'string' },
    status: { type: 'string', enum: ['complete', 'partial', 'blocked'] },
    files_changed: { type: 'array', items: { type: 'string' } },
    files_deleted: { type: 'array', items: { type: 'string' } },
    emergent_decisions: { type: 'array', items: { type: 'string' }, description: 'choices made beyond _DECISIONS.md that later steps must honor (esp. final symbol names/signatures)' },
    deviations: { type: 'array', items: { type: 'string' }, description: 'anywhere you departed from _DECISIONS.md or the plan, with why' },
    self_check: { type: 'string', description: 'the ruff + import commands you ran and what you saw' },
    notes_for_next_step: { type: 'string' },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['ruff_ok', 'import_ok', 'first_failure', 'targeted_summary', 'detail'],
  properties: {
    ruff_ok: { type: 'boolean' },
    import_ok: { type: 'boolean' },
    first_failure: { type: ['string', 'null'], description: 'the single most important failure line, or null if ruff+import both pass' },
    targeted_summary: { type: 'string', description: 'pass/fail counts of the targeted tests (advisory; may be red mid-refactor)' },
    detail: { type: 'string' },
  },
}

const COMMON = `You are one step of a coherence-bound, dependency-ordered refactor of the EphemeralOS task_center (the "reducers + unified outcomes redesign").
AUTHORITATIVE INPUTS — read BOTH in full before editing:
  - Spec: ${PLAN}
  - Settled decisions (FINAL names/signatures, the 7-step order, the WF-B partition): ${DECISIONS}
Also read the per-cluster seam manifests under docs/plans/seam_maps/ that your step references — they hold precise current-code anchors.
Working dir = repo root. Source under backend/src, tests under backend/tests. Use .venv/bin/ruff and PYTHONPATH=backend/src .venv/bin/python.

NON-NEGOTIABLE RULES:
1. Make ONLY your step's edits per _DECISIONS §3 for THIS step. Do not do a later step's work; do not opportunistically refactor.
2. The package MUST remain importable at the end of your step. Where _DECISIONS §3 says to leave a re-export shim (e.g. the 3 old */state.py paths after Step 2), DO leave it — a later step removes it.
3. Update the CORE-COUPLED tests + core mock seams for your step (the files _DECISIONS §4 lists under "Explicitly NOT WF-B" and "CORE shared mock seams" that belong to your step). Do NOT touch files owned by WF-B groups G1–G10 (the _DECISIONS §4 table) — a later parallel pass handles those.
4. Honor the FINAL names in _DECISIONS §1 exactly (e.g. Outcome.text, AttemptStage RUN, AttemptFailReason TASK_FAILED, reducer_task_id ":red:", submit_reduction_success/failure, submit_workflow_handoff). If you must depart, record it in "deviations".
5. Match existing code style; keep the touched file's final shape minimal (net-negative where the plan deletes). Remove imports/symbols YOUR change orphaned.
6. BEFORE returning, self-check: run \`.venv/bin/ruff check backend/src\` and \`PYTHONPATH=backend/src .venv/bin/python docs/plans/seam_maps/_deep_import.py\` (a DEEP import of EVERY submodule — a plain \`import task_center\` is a lazy facade and hides broken lifecycle imports). The deep import must end \`DEEP_IMPORT ok=N bad=0\`. Fix anything you broke. Report what you ran in self_check.

Prior steps' reports (honor their emergent_decisions):
__PRIOR__`

const STEPS = [
  {
    phase: 'Step1-reducer-foundation',
    label: 'step1-reducer-foundation',
    targeted: 'backend/tests/unit_test/test_task_center/test_lifecycle/test_agent_launch_factory_for_role.py',
    scope: `STEP 1 — Reducer foundation (WS1 + WS3). Follow _DECISIONS §3 Step 1 + §1.2 + §2 (executor.md/skills) + cluster1 manifest.
Do: agents/definition/model.py + loader.py (AgentRole.EVALUATOR->REDUCER="reducer"; fix the stale "GENERATOR covers executor and verifier" docstring -> executor-only; loader role-error string ~L65-70); _core/task_state.py (TaskCenterTaskRole.EVALUATOR->REDUCER; DELETE SpawnReason enum + any TERMINAL set membership it had); _core/primitives.py (DROP evaluator_task_id; ADD reducer_task_id(attempt_id, local_id)->f"{attempt_id}:red:{local_id}", root_task_id(run_id)->f"{run_id}:root", attempt_id_from_task_id(task_id)->str|None per §1.2; update __all__);
RENAME pkg tools/submission/evaluator/ -> tools/submission/reducer/ with terminals submit_reduction_success/submit_reduction_failure (binary; input field summary:str min_length=1; DROP passed_criteria/failed_criteria args; submission_kind "reduction_success"/"reduction_failure"); DELETE tools/submission/verifier/; rename tools/submission/executor/submit_execution_handoff -> submit_workflow_handoff (keep goal_handoff arg); tools/_names.py constant renames per §1.2; tools/submission/_factory.py wiring; tools/_terminals/registry.py descriptors;
profiles agents/profile/main/: evaluator.md->reducer.md (vocab + drop criteria framing), DELETE generator_verifier.md, executor.md vocab (evaluator->reducer, submit_execution_handoff->submit_workflow_handoff at the L26/37/39/51/52 sites), update executor_routing.py + any *_routing.py referencing renamed terminals;
config/skills/: rename skills/evaluator/ -> skills/reducer/ dir (loader hard-fails otherwise), reframe config/skills/{executor,reducer}/SKILL.md to drop <plan_spec>/<evaluation_criteria> and use <needs>/<assigned_task>/<assigned_prompt> (§2);
task_center_runner/core/bootstrap.py _REQUIRED_AGENT_NAMES (drop verifier, evaluator->reducer); context_engine/agent_directives.py (drop verifier; evaluator->reducer directive per §1.6); agent_launch/task_guidance_dispatch.py role dispatch; audit role SETS in task_center_runner/audit/recorder.py + node_id.py (evaluator->reducer, drop verifier);
CORE mock seams for step1 vocab: scenarios/base.py (evaluator_response->reducer_response, delete verifier_response), agent/mock/scenario_adapter.py (_reducer_script, delete _verifier_script, role dispatch, handoff rename), test_task_center/conftest.py (reducer agent fixture, delete verifier, handoff rename).
NOTE: apply_evaluator_submission/apply_reducer_submission and the reducer recipe do NOT exist yet — the reducer terminal's apply hook may call a not-yet-renamed method; if so, leave a minimal TODO-stub that keeps imports working (Step 4/5 wires the real path). Keep it importable.
Do NOT touch: WF-B groups (esp. G10 vocab tests under test_agents/test_tools/test_benchmarks — later parallel pass), the stage machine, db models, recipes (beyond agent_directives + the evaluator.md->reducer.md profile).`,
  },
  {
    phase: 'Step2-state-consolidation',
    label: 'step2-state-consolidation',
    targeted: 'backend/tests/unit_test/test_task_center/test_domain',
    scope: `STEP 2 — State consolidation + enum/tuple shapes (WS6 D11 + WS2/WS4 field bodies). Follow _DECISIONS §3 Step 2 + §1.3 + §1.4 + §1.5 + cluster4/cluster2 manifests.
Author backend/src/task_center/_core/state.py absorbing Workflow + Iteration + Attempt + the 6 lifecycle enums:
  - WorkflowStatus, IterationStatus, IterationCreationReason, AttemptStage(PLAN|RUN|CLOSED), AttemptStatus(RUNNING|PASSED|FAILED), AttemptFailReason(TASK_FAILED="task_failed"|STARTUP_FAILED="startup_failed").
  - Workflow: id, task_center_run_id, workflow_goal (was goal), status, iteration_ids, parent_task_id: str|None (NEW), created/updated/closed_at, is_open. DROP final_outcome, origin_kind, requested_by_task_id, .origin property, WorkflowOriginKind, WorkflowOrigin, WorkflowClosureReport(+to_final_outcome), WorkflowClosureDeliveryResult.
  - Iteration: id, workflow_id, sequence_no, creation_reason, iteration_goal (was goal), attempt_budget, status, attempt_ids, deferred_goal_for_next_iteration, outcomes: str|None (was task_summary; Text holding JSON), created/updated/closed_at + the existing properties. DROP plan_spec. DROP TerminalSuccess/SuccessDeferred/AttemptPlanFailed/ClosureOutcome/IterationClosureReport.
  - Attempt: id, iteration_id, attempt_sequence_no, stage, status, planner_task_id, plan_spec (KEEP), generator_task_ids, reducer_task_ids (NEW), deferred_goal_for_next_iteration, fail_reason, created/updated/closed_at, is_closed. DROP evaluation_criteria, evaluator_task_id.
Leave TEMPORARY re-export shims at workflow/state.py, iteration/state.py, attempt/state.py that import-and-re-export from _core.state (so every existing importer still works; Step 7 deletes the shims).
CRITICAL import-survival rule: orchestrator.py, starter.py, and attempt_coordinator.py import the closure DTOs (WorkflowClosureReport, WorkflowOrigin, WorkflowOriginKind, WorkflowClosureDeliveryResult, IterationClosureReport, ClosureOutcome, TerminalSuccess, SuccessDeferred, AttemptPlanFailed) AT MODULE LOAD. You are renaming/dropping their canonical fields but you may NOT break those imports in this step. So: DEFINE these closure DTOs in _core/state.py too, as TEMPORARY (mark each with a "# TEMP: removed in Step 5/7" comment) — keeping the legacy goal/final_outcome/origin fields they need — and re-export them through the 3 shims. Step 5 removes the iteration/attempt closure DTOs with attempt_coordinator's use; Step 7 removes the workflow ones with the closure layer. The deep-import gate (every submodule) MUST stay green after this step. Record the temp-retained list in notes_for_next_step.
Repoint the _core importers (outcomes/generator_summaries.py imports of Attempt/AttemptFailReason/IterationStatus, invariants.py, persistence.py) to task_center._core.state.
Update CORE-COUPLED tests: test_domain/test_workflow_dto.py, test_iteration_dto.py, test_attempt_dto.py (new field names/enums). test_domain/test_iteration_closure_report.py + test_ancestry.py are DELETED in Step 7 — leave them for now (they still import the shim).
Keep importable. Do NOT yet touch db models/stores (Step 4), orchestrator/stage machine (Step 5), or the closure/starter logic (Step 7) beyond what's needed to keep imports working via shims.`,
  },
  {
    phase: 'Step3-outcome-type-root',
    label: 'step3-outcome-type-root',
    targeted: 'backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_other.py',
    scope: `STEP 3 — Outcome type root (WS4 type). Follow _DECISIONS §3 Step 3 + §1.1 + cluster3 manifest.
Rename backend/src/task_center/_core/generator_summaries.py -> _core/outcomes.py (git-mv semantics; update all importers). Changes:
  - TaskOutcome -> Outcome; field summary -> text; keep is_terminal, raw_status, children, failure, local_id, status.
  - to_record emits key "text"; from_record reads "text" then falls back to "summary" (legacy pre-migration rows).
  - parse_achieved_record -> parse_outcomes_record (legacy free-text branch builds Outcome(local_id="summary", status="success", text=str(value))).
  - ADD reducer_outcomes(attempt, *, task_store) mirroring generator_outcomes over attempt.reducer_task_ids.
  - ADD workflow_outcomes(workflow, *, iteration_store) = last iteration's outcomes (parse_outcomes_record of the last iteration row's outcomes). Used later by run-report + root close.
  - Rewrite attempt_failure_line(attempt, *, task_store): STARTUP_FAILED->"agent_launch_failed"; TASK_FAILED-> render failed plan task lines (role-generic) over generator_task_ids ∪ reducer_task_ids via a shared _failed_task_lines(...) helper (used again by WS5 retry + failure-aware close). The old PLANNER_FAILED/EVALUATOR_FAILED/GENERATOR_FAILED branches are gone (AttemptFailReason now only TASK_FAILED|STARTUP_FAILED).
  - REMOVE latest_task_summary (a private _latest_text(rows) helper is allowed to dedupe the summaries[-1] walks). _handoff_rollup/child_outcomes_for_workflow: keep functioning for now (Step 7 reshapes them) — do NOT delete here, just repoint imports.
  - Update __all__ + the module docstring (mentions of summaries/<task>).
Repoint importers: orchestrator.py, attempt_coordinator.py, generator_dag.py(if any), recipes/_task_xml.py, and any test. Note attempt_failure_line callers.
Update/ADD the Outcome round-trip test (§10): if no test exists, add backend/tests/unit_test/test_task_center/test_domain/test_outcomes_roundtrip.py asserting to_record/from_record incl. legacy "summary".
Keep importable (ruff+import green).`,
  },
  {
    phase: 'Step4-db-stores-protocols',
    label: 'step4-db-stores-protocols',
    targeted: 'backend/tests/unit_test/test_task_center/test_persistence',
    scope: `STEP 4 — Store + protocol signatures (MN3) BEFORE callers. Follow _DECISIONS §3 Step 4 + §1.7 + DRIFT-A + cluster7 manifest. This is large — be systematic, file by file.
db/engine.py migration maps EXACTLY per §1.7 (DRIFT-A: _DROPPED_COLUMNS["attempts"]={evaluation_criteria, evaluator_task_id, plan_spec} — ADD the whole key, it doesn't exist today; _DROPPED_COLUMNS["iterations"]={"plan_spec"}; _DROPPED_COLUMNS["workflows"]={final_outcome, origin_kind, requested_by_task_id}; task_center_tasks DROP {fix_target_id, context_packet_id, task_center_attempt_id, spawn_reason} added to existing; _RENAMED_COLUMNS: iterations task_summary->outcomes, task_center_tasks summaries->outcomes; REMOVE obsolete task_specification->plan_spec rename entries for iterations+attempts). New columns are ADDs via the ORM (_add_missing_columns), not renames.
db/models/{workflow,iteration,attempt,task_center}.py: column drops/adds per §1.7 — workflows ADD parent_task_id, DROP final_outcome/origin_kind/requested_by_task_id, goal column stays the same name OR rename? (keep DB column name 'goal' if that's what exists; the DTO field is workflow_goal — map in the store. Verify current column name in cluster7 manifest and keep PK 'id'.) iterations RENAME task_summary->outcomes (Text), DROP plan_spec, goal column. attempts ADD reducer_task_ids, DROP evaluation_criteria/evaluator_task_id/plan_spec? NO — plan_spec is KEPT on Attempt DTO; but §1.7 drops attempts.plan_spec column?? RECONCILE: _DECISIONS §1.2/§1.3 KEEP Attempt.plan_spec, but §1.7 DROPS the attempts.plan_spec column. This is a conflict — resolve by KEEPING the attempts.plan_spec column (Attempt.plan_spec is live, set by set_plan_contract) and REMOVING plan_spec from _DROPPED_COLUMNS["attempts"] (so that key = {evaluation_criteria, evaluator_task_id}); record this reconciliation in deviations. task_center_tasks: summaries->outcomes (JSON column), ADD terminal_tool_result (JSON) + child_workflow_id (str), DROP fix_target_id/context_packet_id/task_center_attempt_id/spawn_reason; PK stays 'id'.
db/stores/* + _core/persistence.py Protocols: upsert_task (summaries->outcomes + terminal_tool_result; drop task_center_attempt_id/fix_target_id/spawn_reason/context_packet_id kwargs), set_task_status* (replace-write outcomes + terminal_tool_result; ADD child_workflow_id param to set_task_status_if_current + the Protocol), close_succeeded (task_summary->outcomes; drop plan_spec param), set_status (drop final_outcome), set_evaluator_task_id->set_reducer_task_ids, set_plan_contract (drop evaluation_criteria; keep plan_spec), workflow insert(parent_task_id; drop origin/requested_by_task_id); DELETE set_task_context_packet_id + list_generator_tasks_for_attempt (+ Protocol entries); re-derive list_tasks_for_attempt by id.like(f"{attempt_id}:%") (live consumer runner.py:95). The TWO _serialize_task serializers (task_center_store.py + audit/recorder.py) + the 3 _serialize_* in recorder: same key renames (id->task_id in serialized dict, summaries->outcomes, drop dropped fields).
Audit task_center_runner/audit/recorder.py + node_id.py: role sets already done in Step1; here apply the serializer key renames (drop evaluator_task_id, summaries->outcomes) so they don't AttributeError after model drops.
Because the orchestrator/coordinator/starter (Steps 5/7) still call OLD signatures right now, keeping the package importable means: change the store/protocol SIGNATURES, and where an existing caller would now pass a removed kwarg AT IMPORT TIME (none — calls are runtime), it's fine. But a caller that references a deleted store METHOD at import is fine too (runtime). Confirm import + ruff green. Update test_persistence/** core-coupled tests to the new signatures/columns (incl. test_migration_drops_legacy_table.py).
Do NOT rewrite orchestrator/stage logic (Step 5) or closure/starter (Step 7).`,
  },
  {
    phase: 'Step5-gate-submit',
    label: 'step5-gate-submit',
    targeted: 'backend/tests/unit_test/test_task_center/test_lifecycle/test_generator_dag.py backend/tests/unit_test/test_task_center/test_lifecycle/test_iteration_attempt_coordinator.py backend/tests/unit_test/test_task_center/test_lifecycle/test_phase03_submission_integration.py',
    scope: `STEP 5 — Gate + submit path (WS2 + WS4 writes). Follow _DECISIONS §3 Step 5 + §1.2 + §1.3 + cluster2 manifest.
attempt/generator_dag.py -> attempt/plan_dag.py: ordered_generator_tasks -> ordered_plan_tasks(generators, reducers)->(ordered_gen, ordered_red) validating unique ids across both, known needs, no cycle, >=1 reducer, reachability (every generator in the reverse-needs-closure of >=1 reducer); GeneratorDagSummary->DagStatus, summarize_generator_dag->dag_status; ready_pending_generator_ids->ready_pending_plan_ids (role-agnostic); DELETE dependency_task_ids (inline the local_id->task_id map in orchestrator where both tuples are in scope, using generator_task_id/reducer_task_id).
tools/submission/planner/_schemas.py: PlanTaskInput.deps->needs; ADD ReducerInput {id, needs, prompt} (prompt nonblank, reuse validate_nonblank); replace evaluation_criteria field with reducers: list[ReducerInput] min 1; build_planner_submission calls ordered_plan_tasks(generators, reducers); _is_generator_capable_agent docstring drop verifier (executor only). KEEP plan_spec field.
task_center/submissions.py: PlannedGeneratorTask.deps->needs; ADD PlannedReducerTask {local_id, needs, prompt} (no agent_name); EvaluatorSubmission->ReducerSubmission {attempt_id, task_id, status:Literal["success","failure"], text, terminal_tool_result}; GeneratorSubmission outcome->status, summary->text, payload->terminal_tool_result; PlannerSubmission: evaluation_criteria->reducers: tuple[PlannedReducerTask,...]; KEEP plan_spec, tasks.
attempt/stage_advancer.py -> attempt/run_stage.py (KEEP class name AttemptStageAdvancer): collapse to single RUN advance over generator_task_ids ∪ reducer_task_ids; DELETE _start_evaluator_stage/_advance_evaluator_stage/_launch_evaluator and the EVALUATE branch; _advance_generator_stage->_advance_run_stage uses ready_pending_plan_ids + dag_status; close: all DONE->PASSED, any failed/blocked->FAILED(TASK_FAILED). Launch ready tasks regardless of role (generator launch via for_generator; reducer launch — reducers are spawned as RUN tasks too: a reducer task is created at plan-persist time like generators, launched when its needs are DONE, via AgentLaunchFactory.for_reducer). Reconcile with Step1's launch.py for_evaluator->for_reducer + REDUCER_AGENT_NAME.
attempt/orchestrator.py: apply_evaluator_submission->apply_reducer_submission; _mark_evaluator->_mark_reducer (assert via reducer assert + attempt.reducer_task_ids); persist BOTH generator + reducer tasks at apply_plan_submission (set_generator_task_ids + set_reducer_task_ids; stage RUN); _write_submission_status writes status->outcomes(one Outcome)+terminal_tool_result (use Outcome/to_record); apply_planner_failure uses AttemptFailReason.TASK_FAILED; drop plan_spec? KEEP (set_plan_contract still sets plan_spec, drop evaluation_criteria). Remove WorkflowClosureReport import (Step 7 handles handoff; if apply_workflow_closure_report is referenced, leave a thin stub for Step 7 OR move its removal to Step 7 — coordinate: KEEP the method working via the shim until Step 7).
attempt/orchestrator_registry.py: RegisteredAttemptOrchestrator Protocol -> add apply_reducer_submission (drop apply_evaluator_submission); keep apply_workflow_closure_report until Step 7.
_core/invariants.py: assert_evaluator_task_for_submission->assert_reducer_task_for_submission (checks attempt.reducer_task_ids); assert_task_belongs_to_attempt rewrite to id-prefix check str(task.get("id") or "").startswith(f"{attempt.id}:").
iteration/attempt_coordinator.py: _achieved_record_for->_iteration_outcomes_for projects REDUCER outcomes (reducer_outcomes); close_succeeded(outcomes=...) (drop plan_spec arg per Step4); failure-aware close: on failed close, write iteration.outcomes = failed-task Outcomes (status="failure", failure=fail line) via the shared _failed_task_lines/outcomes (set_status optional outcomes kwarg). Remove IterationClosureReport/ClosureOutcome usage if the closure layer is gone — BUT the on_iteration_closed callback chain still exists until Step 7; coordinate: keep emitting the lifecycle signal Step 7 expects (if Step 7 hasn't run, keep IterationClosureReport via shim). Record any such bridge in deviations.
NEW gate scenarios are NOT here (CORE-authored later or in WS9). Update core-coupled tests: test_lifecycle/test_generator_dag.py (rename to test_plan_dag or keep filename; new >=1-reducer/reachability asserts), test_iteration_attempt_coordinator.py (reducer projection), test_phase03_submission_integration.py.
Keep importable. This step has cross-cutting touch with Step 7's closure removal — prefer leaving thin bridges (documented in deviations) over breaking imports.`,
  },
  {
    phase: 'Step6-recipes-prompts',
    label: 'step6-recipes-prompts',
    targeted: 'backend/tests/unit_test/test_task_center/test_context_engine',
    scope: `STEP 6 — Recipes + prompts (WS5 + M2). Follow _DECISIONS §3 Step 6 + §1.6 + §2 + cluster5 manifest.
context_engine/recipes/_needs.py (NEW): needs_outcome_blocks(*, needs, task_store)->list[ContextBlock] extracted from generator _dependency_blocks; group tag "dependency"/"dependencies" -> "needs"; child tag stays "task". Shared by generator + reducer.
recipes/evaluator.py -> recipes/reducer.py: per-task recipe (_REQUIRED_FIELDS {workflow_id, attempt_id, task_id}), reads ONLY its needs outcomes (needs_outcome_blocks) + its own prompt (task.context_message rendered as <assigned_prompt>). No plan_spec/evaluation_criteria/all-generator reads.
recipes/generator.py: delete the dead attempt.plan_spec <plan_spec> block; call needs_outcome_blocks; render <assigned_task>.
recipes/planner.py: R1a fold iterations.py + attempts.py in then DELETE both; retry body renders failed-TASK outcomes (any role) + <failure>; DROP <evaluator_summary>/_evaluator_summary_if_ran; relay renders prior iterations' reducer outcomes from iteration.outcomes (parse_outcomes_record). Emit <workflow_goal> + <iteration_goal> (D2; pin these exact tags). Update the planner attempt-history block to position=/no stale criteria tags.
context_engine/core.py -> engine.py (module rename; repoint the ~6 src importers + lazy _EXPORTS).
scope.py: ContextScope.for_evaluator->for_reducer (gains task_id, mirror for_generator).
tag_dictionary.py + renderer.py: delete plan_spec/evaluation_criteria/evaluator_summary descriptors + _DEFAULT_TAGS["task_specification"]; dependency->needs; add assigned_prompt (reducer prompt block reuses ContextBlockKind.PLANNED_TASK_SPEC with metadata["tag"]="assigned_prompt"); drop dead ContextBlockKind.TASK_SPECIFICATION if unused. recipes_registry.py: register reducer recipe (drop evaluator). packet.py / context_outline.py / task_guidance.py: vocab.
agents/profile/main/planner.md: apply §2 M2 edits (drop plan_spec + evaluation_criteria from signatures L60/64, field list L82-93, validity L104, output-discipline L110-122; tasks items deps->needs; drop verifier; add reducers field bullet + "each reducer's prompt is the acceptance authority" sentence; L44 position=, L46 failed-task outcomes + <failure>; evaluator->reducer). reducer.md already created in Step1 — refine if needed.
CORE mock seam: agent/mock/scenario_loop_runner.py _inspect_prompt role branches + the <goal> vs <workflow_goal>/<iteration_goal> string checks (loop_runner ~:250,258,303) to match the tags you emit. pack_catalog.py:138 context.evaluator_iterative_deferral->context.reducer_... in lockstep.
Update core-coupled context-engine tests: test_engine.py, test_scope.py, test_packet.py, test_recipes_*.py, test_role_context_matches_diagram.py, test_recipes_other.py, test_renderer.py, test_tag_dictionary.py, test_context_outline.py, test_task_guidance.py, test_attempts*.py (the <dependency>-><needs>, evaluator->reducer, plan_spec drop). (These overlap WF-B G7 — since they are behavior-coupled to the recipe shape, OWN them HERE.)
Keep importable.`,
  },
  {
    phase: 'Step7-closure-handoff-root',
    label: 'step7-closure-handoff-root',
    targeted: 'backend/tests/unit_test/test_task_center/test_lifecycle backend/tests/unit_test/test_task_center/test_domain',
    scope: `STEP 7 — Closure removal + handoff + root (WS7 + WS8) — LAST. Follow _DECISIONS §3 Step 7 + §1.4 + cluster4 manifest + spec §6.
DELETE workflow/closure_report_router.py, workflow/ancestry.py, attempt/deps.py, and the 3 re-export shims workflow/state.py + iteration/state.py + attempt/state.py (everything now imports task_center._core.state).
attempt/deps.py removal: move AgentLaunch + AttemptDeps into attempt/launch.py (cycle-free); update importers (__init__.py, entry/bootstrap.py, starter.py, run_stage.py, orchestrator.py, tools/submission/context/attempt.py:11, attempt/launch.py:16). AttemptDelegatedWorkflowParentTask dissolved into the 3 orchestrator handoff methods.
_core/terminal_tool_routing.py -> _core/terminal_routing.py: fold nested_workflow_depth (from ancestry.py) as a private helper using Workflow.parent_task_id + attempt_id_from_task_id (no task_center_attempt_id).
workflow/state.py deletions cascade: WorkflowOrigin/Kind, closure DTOs already moved out in Step 2 — finish removing any stragglers.
attempt/orchestrator.py handoff: replace apply_workflow_closure_report + _build_handoff_rollup with the 3 methods per §1.4: start_child_workflow(*, generator_task, child_workflow) (atomic RUNNING->WAITING_WORKFLOW + child_workflow_id via set_task_status_if_current(child_workflow_id=...)); apply_child_workflow_outcome(*, generator_task, child_workflow, final_attempt_id) (write generator outcomes = ONE Outcome whose children = workflow_outcomes(child_workflow) (MN2); DONE/FAILED; advance DAG); cancel_child_workflow(*, generator_task) (restore RUNNING). M1 orphan-guard: if start/cancel fail, force WAITING_WORKFLOW->FAILED via set_task_status_if_current.
workflow/lifecycle.py: close-routing fork on the closing workflow's parent_task_id — root (ends ":root") -> run_close_handler (RunController.on_root_workflow_closed); attempt-prefixed -> orchestrator_registry.get(attempt_id).apply_child_workflow_outcome. Replace deliver_closure_report seam with run_close_handler + registry lookup. iteration close (the on_iteration_closed chain) -> derive iteration.outcomes (passing = reducer outcomes; failed = failed-task outcomes) and workflow status; remove IterationClosureReport.
workflow/starter.py: start(*, prompt, parent_task_id: str) single path (no WorkflowOrigin); _prepare/_mark_parent_waiting -> the atomic flip+link; relax :143 attempt-bound + :171 RUNNING guards for the ":root" parent; compensation path drops the synthetic-closure-report (M1 lives in orchestrator + RunController failsafe).
NEW backend/src/task_center/run_controller.py per §1.4 + spec §6: RunController(*, runtime); start_root_run(*, prompt, task_center_run_id)->StartedWorkflow (seed synthetic GENERATOR root_task_id RUNNING, then WorkflowStarter.start(parent_task_id=root_task_id); any throw -> _finish_run_if_open(run_id, status="failed") + re-raise); on_root_workflow_closed(*, child_workflow) (idempotent; bootstrap outcomes=workflow_outcomes(child_workflow), DONE/FAILED, finish_run); _finish_run_if_open moved here from bootstrap.py.
entry/bootstrap.py: replace the entry-origin WorkflowStarter.start with RunController.start_root_run; wire run_close_handler so lifecycle routes root closes to RunController.
db: workflow_store/workflow.py parent_task_id (Step4 added the column; here wire insert/serialize); _core/persistence set_status already dropped final_outcome (Step4).
run-report task_center_runner/core/runner.py _graph_summary (~L127-137): surface workflow.status + derived workflow_outcomes(workflow) + parent_task_id (drop final_outcome).
Delete now-dead tests: test_domain/test_ancestry.py, test_domain/test_iteration_closure_report.py, test_lifecycle/test_phase04_close_report_delivery.py (if it asserts the deleted router) — or rewrite to the new handoff. Update test_lifecycle/test_workflow_lifecycle.py, test_entry_bootstrap.py, test_phase04_deferred_retry.py, _scenario_helpers/workflow_origin.py (origin removed -> parent_task_id.endswith(":root")).
FINAL: grep -rn for old module/symbol names (WorkflowOrigin, closure_report_router, ancestry, deps.AttemptDeps old path, summarize_generator_dag, evaluator_task_id, submit_execution_handoff) across backend/src — none should remain in SRC (tests in WF-B groups may still have them; that's the next pass). Keep importable.`,
  },
]

let priorReports = []

for (const s of STEPS) {
  phase(s.phase)
  const priorText = priorReports.length
    ? priorReports.map((r) => `--- ${r.step} (${r.status}) ---\nemergent_decisions: ${JSON.stringify(r.emergent_decisions)}\ndeviations: ${JSON.stringify(r.deviations)}\nnotes_for_next_step: ${r.notes_for_next_step}`).join('\n\n')
    : '(none — you are the first step)'
  const prompt = COMMON.replace('__PRIOR__', priorText) + `\n\n=== YOUR STEP ===\n${s.scope}`

  const impl = await agent(prompt, { label: s.label, phase: s.phase, schema: IMPLEMENT_SCHEMA, model: 'opus' })
  if (!impl) {
    return { halted: true, failedStep: s.phase, reason: 'implement agent returned null (skipped)', priorReports }
  }
  priorReports.push(impl)

  const verify = await agent(
    `You are an independent verifier for step "${s.phase}" of the task_center refactor. Run EXACTLY these and report:
1. \`.venv/bin/ruff check backend/src 2>&1 | tail -50\`  -> ruff_ok = "All checks passed!" (or zero errors).
2. \`PYTHONPATH=backend/src .venv/bin/python docs/plans/seam_maps/_deep_import.py 2>&1 | tail -30\` -> import_ok = the final line reads "DEEP_IMPORT ok=N bad=0". Any "FAIL <module>" line means import_ok=false and first_failure = that first FAIL line. (This deep-imports every submodule; a plain \`import task_center\` would hide broken lifecycle imports.)
3. (advisory) \`.venv/bin/python -m pytest ${s.targeted} -q -p no:cacheprovider 2>&1 | tail -20\` from repo root -> targeted_summary = the pass/fail line. This MAY be red mid-refactor (later steps complete the picture) — report it but it does NOT gate.
Report ruff_ok, import_ok, first_failure (the single most important ruff/import failure line, or null if both pass), targeted_summary, and detail. Do NOT edit any files.`,
    { label: `verify-${s.label}`, phase: s.phase, schema: VERIFY_SCHEMA, model: 'sonnet' }
  )

  log(`${s.phase}: ruff=${verify?.ruff_ok} import=${verify?.import_ok} | ${verify?.targeted_summary || ''}`)

  if (!verify || !verify.ruff_ok || !verify.import_ok) {
    return {
      halted: true,
      failedStep: s.phase,
      verify,
      implReport: impl,
      priorReports,
      message: `Halted at ${s.phase}: ruff_ok=${verify?.ruff_ok} import_ok=${verify?.import_ok}. ${verify?.first_failure || ''}`,
    }
  }
}

return { halted: false, completed: STEPS.length, priorReports }
