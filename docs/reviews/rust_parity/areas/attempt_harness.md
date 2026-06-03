# Rust Parity Audit — Attempt Harness (agent-core)

Area: Attempt harness (planner DAG, generator/reducer, PLAN->RUN->CLOSED, reducer exit gate).
Python = behavioral ground truth; `docs/architecture` = corroboration. Evidence is bilateral file:line.

## Ground truth

Docs (corroboration):
- `docs/architecture/workflow/attempt-harness.html` — §1 state machine (PLAN->RUN->CLOSED, line 61); §2 planner/plan-DAG persistence + structural gate (lines 100-106); §3 plan-DAG waves + quiescence (lines 119-149); §4 RUN-stage closure / reducer-as-judge (lines 154-162); §5 launcher failures + exhaustion (lines 170-203); §6 audit events `workflow.task.ready|launched|failed` (lines 207-216).
- `docs/architecture/workflow/agent-roles.html` — role catalog (planner/generator/reducer).

Python (truth), `backend/src`:
- `workflow/attempt/orchestrator.py` — `AttemptOrchestrator`: `start` (74-104), `apply_plan_submission` (106-139), `apply_planner_failure` (141-150), `apply_generator_submission`/`apply_reducer_submission` (152-160), `_close_attempt` (246-267), `_mark_startup_failed` (269-289).
- `workflow/attempt/launch.py` — `AttemptDeps` (89-124), `EphemeralAttemptAgentLauncher` (132-281), `_fail_unowned_attempt` (284-331), `_report_exhaustion` (350-392), `AgentLaunchFactory` (402-502).
- `workflow/attempt/plan_dag.py` — `ordered_plan_tasks` (34-74), `_assert_lane_shape` (77-122), `_assert_acyclic`/`_topo_order` (125-149), `ready_pending_plan_ids` (158-169), `dag_status`/`_unreachable_pending_ids` (194-255).
- `workflow/attempt/run_stage.py` — `AttemptStageAdvancer.advance_ready_tasks`/`_advance_run_stage` (54-89), `_launch_ready_plan_task` (131-170), `_mark_launch_failed` (102-129).
- `workflow/attempt/orchestrator_registry.py` — `AttemptOrchestratorRegistry` (39-67).
- Boundary (off-spine, but the plan-DAG **validation + persistence + topo-order** live here in Python): `tools/submission/planner/_schemas.py` `build_planner_submission` (148-261), calls `ordered_plan_tasks` (200) and `_is_generator_capable_agent` role gate (136-145).
- `workflow/submissions.py` (DTOs), `workflow/_core/invariants.py` `assert_generator_task_for_submission`/`assert_reducer_task_for_submission` (131-140), `task/task.py` `TERMINAL_GENERATOR_STATUSES = {DONE,FAILED,BLOCKED}` (29-35), `AttemptStage` PLAN/RUN/CLOSED (`workflow/_core/state.py` 120-123).

## Rust mapping

Under `agent-core/crates/eos-workflow/src/attempt/`:
- `orchestrator.rs` — `AttemptOrchestrator`: `start` (64-115), `spawn_planner_run`/`apply_planner_report`/`synthesize_planner_failure` (117-176), `apply_plan`/`materialize_plan_tasks`/`validate_plan_shape`/`assert_acyclic` (179-384, 742-797), `apply_plan_submission` (386-443), `apply_planner_failure` (446-467), `apply_generator_submission`/`apply_reducer_submission` (470-489), `record_generator_submission`/`record_reducer_submission` (491-543), `mark_execution_task` (545-597), `close_attempt` (599-644), `assert_stage`/`validate_planner_submission` (672-715).
- `run_stage.rs` — `AttemptStageAdvancer.advance_run_stage` (41-112, JoinSet loop + `max_concurrent_task_runs` cap), `build_launch` (114-135), `mark_launch_failed` (137-193), `apply_report`/`apply_terminal`/`synthesize_failure` (195-288).
- `plan_dag.rs` — `ready_pending_plan_ids` (19-32), `dag_status`/`unreachable_pending_ids`/`is_unreachable` (34-138). NOTE: no `ordered_plan_tasks` (no topo sort).
- `launch.rs` — `AttemptDeps` (96-177, adds `runner`, `max_concurrent_task_runs`), `AgentRunner` trait + `AgentRunReport`/`AgentTerminal` (19-66), `AgentLaunchFactory` (179-335).
- `orchestrator_registry.rs` — `AttemptOrchestratorRegistry` (11-56).
- `mod.rs` — re-exports (1-14).

Boundary pieces (validation split differs from Python — see invariant 1):
- `eos-tools/src/model_tools/submission.rs` — `SubmitPlannerOutcome` tool: `validate_planner_input` (419-467), `validate_planner_structure` (471-495, duplicate **generator** ids + missing/extra specs only).
- `eos-tools/src/ports.rs` — `PlannerPlan`/`PlanTask`/`PlanReducer` (104-151), `PlanSubmissionPort` (167-...).
- `eos-workflow/src/ports.rs` — `PlanSubmissionAdapter` (26-84): `apply_plan`/`submit_generator`/`apply_reducer` route to the orchestrator's **advancing** variants.
- `eos-runtime/src/agent_runner.rs` — `RuntimeAgentRunner` (28-106): production runner, **Phase-6 stub** that always returns `no_terminal`.

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | Each Attempt owns ONE planner-authored DAG of generator + reducer Task rows whose edges are `needs` | partial | medium | `plan_dag.py:34-122` (`ordered_plan_tasks` validates combined gen+red DAG, **combined** dup-id check 47-55); persisted in `tools/.../_schemas.py:222-249` | `orchestrator.rs:184-384` (`materialize_plan_tasks`+`validate_plan_shape`+`assert_acyclic`); dup-id check split to `submission.rs:471-495` | DAG gate ported, but combined dup-id check is incomplete (D1) and topo-order dropped (D2). Validation relocated from tool layer into the orchestrator — intentional. |
| 2 | Attempt stages PLAN -> RUN -> CLOSED | match | — | `state.py:120-123` PLAN/RUN/CLOSED; `orchestrator.py:138` set RUN; `run_stage.py:60` | `eos-state/src/attempt.rs:17-24` Plan/Run/Closed; `orchestrator.rs:438` set Run; `run_stage.rs:51` | Exact 3-stage parity; no leftover GENERATE/EVALUATE. |
| 3 | Reducer is the EXIT GATE; attempt closes through the reducer | match | — | `run_stage.py:80-89` `dag_status`->`_close_attempt`; reducer is a plan task (`plan_dag.py:64,92-102`) | `run_stage.rs:79-96` `dag_status`->`close_attempt`; reducer plan task (`orchestrator.rs:262-295,349-373`) | Closure is plan-DAG quiescence; reducer is the terminal sink. Tests `reducer_is_exit_gate`/`failed_reducer_closes_attempt_failed` (orchestrator.rs:819-921). |
| 4 | Generators + reducers launched per planned tasks, respecting `needs` edges | partial | high | `run_stage.py:54-89,131-170` store-driven re-entrant launch of ALL ready tasks; launcher async (`launch.py:157-181`) | `run_stage.rs:41-112` single JoinSet loop with `max_concurrent_task_runs` cap | Needs-respecting selection matches (`ready_pending_plan_ids`). But Rust adds a per-attempt concurrency cap with no Python analogue (D3), uses a different drive model (D4), AND the production runner never yields a terminal (D5) so the run stage cannot complete a real generator/reducer in production today. |
| 5 | AttemptOrchestrator is per-Attempt, not a global layer | match | — | `orchestrator_registry.py:39-67` lookup-by-id; `orchestrator.py:50-72` one attempt | `orchestrator_registry.rs:11-56` `HashMap<AttemptId, Arc<AttemptOrchestrator>>`; `orchestrator.rs:31-58` one attempt | Process-local registry keyed by attempt id; no global orchestrator. |

Additional constant/operator checks (all match):
- Quiescence set `TERMINAL_GENERATOR_STATUSES = {DONE,FAILED,BLOCKED}` (`task.py:29-35`) == `is_terminal_generator` `matches!(Done|Failed|Blocked)` (`eos-state/src/task.rs:32-37`). MATCH.
- Failure set `_FAILED_OR_BLOCKED = {FAILED,BLOCKED}` (`plan_dag.py:179`) == `matches!(Failed|Blocked)` (`plan_dag.rs:46,126`). MATCH.
- Readiness: pending AND `all(dep == DONE)` — `plan_dag.py:164-168` (`statuses[dep] == DONE`) == `plan_dag.rs:24-31` (`*s == Done`). MATCH (`==`, not `>=`).
- Close validity: failed requires fail_reason, passed forbids it — `orchestrator.py:251` (`assert_valid_attempt_close`) == `orchestrator.rs:604-618`. MATCH.
- `AttemptFailReason`: only TASK_FAILED + STARTUP_FAILED (doc §1 line 63; `apply_planner_failure` closes TASK_FAILED on both sides). MATCH.

## Disparities

### D1 — Combined duplicate-id check lost; generator/reducer id collision corrupts the DAG (bug)
Severity: medium.
Python `ordered_plan_tasks` builds its id map over the **combined** `(*generators, *reducers)` set and raises on any duplicate local id across both groups (`plan_dag.py:45-55`). The Rust port split validation: the tool layer `validate_planner_structure` only de-dups **generators** (`submission.rs:472-479`, `for task in &input.tasks`), and the orchestrator's `validate_plan_shape` uses `BTreeSet`s (`orchestrator.rs:313-317`) where a generator id and a reducer id that collide simply coexist. Then in `materialize_plan_tasks` the same `local_to_task` map is filled from generators (`orchestrator.rs:198-201`) then reducers (`202-205`); a colliding id `"x"` is inserted as `<attempt>:gen:x` and **overwritten** by `<attempt>:red:x`. The generator then resolves its own id and `needs` against the overwritten entry (`209-222`), so the same persisted task id lands in BOTH `generator_task_ids` and `reducer_task_ids`. Python rejects this submission outright with `"Plan contains duplicate local ids: ..."`.
Why it matters: a planner that reuses an id across the two lanes silently produces a malformed plan (duplicate persisted id, lost generator row, projected-outcome double counting) instead of a clean rejection. Reducer-vs-reducer duplicates are likewise uncaught (BTreeSet collapse).
Suggested fix: in `validate_plan_shape`, detect duplicate ids across the **union** of `plan.tasks` and `plan.reducers` (mirror `plan_dag.py:47-55`) and return `"plan contains duplicate local ids: ..."` before building `local_to_task`.

### D2 — Topological ordering of plan tuples dropped; projected-outcome order is model-visible (divergent)
Severity: low.
Python `ordered_plan_tasks` returns both tuples in stable Kahn topo order (`plan_dag.py:70-74`) and persists/records `generator_task_ids`/`reducer_task_ids` in that order (`_schemas.py:222-257`); doc §2 line 105 states "A stable Kahn sort orders both tuples together." Rust `materialize_plan_tasks` validates acyclicity (`assert_acyclic`, orchestrator.rs:383,742-797) but persists/records ids in **raw plan-submission order** (`orchestrator.rs:207-305`); the topo order is computed only to detect cycles and discarded.
Why it matters: `project_attempt_outcomes` iterates `generator_task_ids.chain(reducer_task_ids)` in stored order and concatenates task outcomes (`eos-state/src/outcomes.rs:108-118`); the same ordering flows into iteration/workflow outcome strings surfaced to the parent/model. So Rust's plan-order vs Python's topo-order changes the order of evidence the model sees. Dispatch correctness is unaffected (RUN is needs-driven), so this is low, not a functional bug.
Suggested fix: reorder `generator_ids`/`reducer_ids` by a Kahn topo rank before recording (reuse the order already computed in `assert_acyclic`), or accept the divergence and update doc §2.

### D3 — Per-attempt concurrency cap (`max_concurrent_task_runs`) has no Python analogue (divergent, new feature)
Severity: low.
Rust gates RUN-stage fan-out at `deps.max_concurrent_task_runs` (default 8, `launch.rs:160`; enforced `run_stage.rs:43-47,56-57`), keeping at most N runs in `JoinSet` at once and leaving surplus ready tasks pending until a slot frees (test `fanout_respects_concurrency_cap`, run_stage.rs:441-483). Python `_advance_run_stage` launches **every** ready task each pass with no cap (`run_stage.py:69-78`); the async launcher tracks all pending runs (`launch.py:155,170-181`).
Why it matters: an intentional resource-control addition, not a regression, but it is a behavioral difference (bounded vs unbounded fan-out) and `max_concurrent_task_runs == 0` is a new failure mode guarded in two places (`orchestrator.rs:732-739`, `run_stage.rs:43-47`).
Suggested fix: none required; document the cap as an intentional Rust addition.

### D4 — RUN-stage drive model: re-entrant store-driven (Python) vs single owning JoinSet loop (Rust) (divergent)
Severity: low-medium.
Python is fully store-driven and re-entrant: each terminal submission calls `advance_ready_tasks` which launches newly-ready tasks and returns; the launcher fires fire-and-forget asyncio tasks that each call back into `apply_generator_submission`/`apply_reducer_submission` → `advance_ready_tasks` again (`orchestrator.py:152-160`, `launch.py:157-181`, `run_stage.py:54-78`). The public submission entry points (used by the production tools) ARE the advancing ones. Rust splits this: `advance_run_stage` owns a `JoinSet`, applies each run's RETURN value via the non-advancing `record_*_submission`, and loops itself (`run_stage.rs:49-111,222-241`); the public advancing variants `apply_generator_submission`/`apply_reducer_submission` (`orchestrator.rs:470-489`) each spin up a **fresh** `AttemptStageAdvancer`/JoinSet.
Why it matters: two ways to feed a terminal exist (runner return-value loop, and the tool port `PlanSubmissionAdapter` → advancing variant, ports.rs:59-83). If both ever fire for the same run, the second hits `task ... is not running` (orchestrator.rs:565-569) — a benign reject today, but the advancing variant also launches a competing JoinSet. Currently safe only because the production runner never returns a terminal (D5), so the tool port is the sole real driver path and the return-value loop is exercised only by test doubles. The duplication is latent risk once D5 is resolved.
Suggested fix: when wiring Phase-7 (D5), pick ONE drive path (return-value capture OR tool-port) so a terminal is never applied twice; the module doc at `agent_runner.rs:1-13` already names this hazard.

### D5 — Production `RuntimeAgentRunner` never yields a terminal; the harness cannot succeed in production (missing / incomplete)
Severity: high. Scope: production wiring / cross-boundary (`eos-runtime`), staged to "Phase-7" — the harness LOGIC in `attempt/` is correct (see mostly-match invariant table); the defect is that no production runner can drive it to completion.
The only non-test `AgentRunner` is `RuntimeAgentRunner` (`eos-runtime/src/agent_runner.rs:52-106`). Its own doc (lines 1-13) states it runs every workflow agent with `plan_submission = None` and **always** returns `AgentRunReport::no_terminal(...)` (line 104) — deferring typed-terminal capture to "Phase-7." Consequence: in production every planner/generator/reducer run is treated as exhaustion (`run_stage.rs:204-220`, `orchestrator.rs:152`), so the planner is synthesized to `run_exhausted` and the attempt closes FAILED/TASK_FAILED before any plan runs. Real plans, generators, and reducers (invariant 4) only execute under the test `QueueRunner`/`ScriptedRunner` doubles, which inject terminals via the return value (`run_stage.rs:314-357`, `orchestrator.rs:819-921`).
Why it matters: against Python — where the live submission tools drive the harness end to end — the Rust harness logic is correct in unit tests but **not wired to a runner that can complete it**. This is documented/known incompleteness, not a silent bug, but it is the single largest functional parity gap in this area: the reducer exit gate (invariant 3) and DAG dispatch (invariant 4) are unreachable in the real runtime.
Suggested fix: complete Phase-7 — give `RuntimeAgentRunner` a capturing `PlanSubmissionPort` (or have it translate the engine's terminal-tool result into `AgentTerminal`) and choose a single non-double-applying drive path per D4.

### D6 — Generator-capability role gate dropped (partial / gap)
Severity: low-medium.
Python rejects any planner generator task whose `agent_name` is not a GENERATOR-role profile: `_is_generator_capable_agent` checks `definition.role == AgentRole.GENERATOR` (`_schemas.py:136-167`, error `"Unknown generator agent ..."`). Rust only checks the agent is **registered** — `validate_planner_structure` has no role check (`submission.rs:471-495`), and `materialize_plan_tasks` does `agent_registry.get(&agent_name)` existence only (`orchestrator.rs:224-229`). A planner could bind a generator slot to a planner/reducer/helper profile and pass validation.
Why it matters: a non-generator profile would be launched as a generator (`build_launch`/`for_generator`, run_stage.rs:124-134), running the wrong role with wrong terminals/tooling.
Suggested fix: add a role check in the tool layer (or registry lookup) requiring `AgentRole::Generator` for `plan.tasks[*].agent_name`, mirroring `_is_generator_capable_agent`.

### D7 — No `_fail_unowned_attempt` (missing-orchestrator) path (divergent, structurally moot)
Severity: low.
Python's launcher handles the case where the orchestrator is missing from the registry at exhaustion time: `_fail_unowned_attempt` marks the task failed, closes the attempt directly TASK_FAILED, and notifies the iteration coordinator (`launch.py:284-347`). Rust has no equivalent: exhaustion is reported from inside `apply_report`/`synthesize_failure` (run_stage.rs:195-288) which already holds the `Arc<AttemptOrchestrator>`, so a "missing orchestrator" can't occur on the return-value path. The tool port path (`PlanSubmissionAdapter`, ports.rs:50-82) does handle a missing orchestrator but only by returning `Rejected("attempt ... is not active")` — it does NOT close the attempt or notify the coordinator.
Why it matters: largely an artifact of the different drive model (the owning orchestrator is always in scope), so the safety net is structurally unnecessary on the return-value path. The gap is that a tool-port submission to a deregistered attempt is silently rejected rather than triggering a fail-safe close; in Python the synthesized-exhaustion path always lands the attempt in a terminal state. Low because deregistration only happens at close.
Suggested fix: none functionally required given the drive model; revisit alongside D4/D5 if the tool-port path becomes a primary driver.

### D8 — Entire workflow-audit subsystem (`workflow.task.ready|launched|failed`) absent (missing)
Severity: medium.
Doc §6 (lines 207-216) and Python emit a defined set of attempt-advancement audit events through `WorkflowAuditEmitter`: `workflow.task.ready` (`run_stage.py:145-149`), `workflow.task.launched` (153), and `workflow.task.failed` (123-129). The Rust attempt harness emits **none** of these. `AttemptDeps.audit_sink` exists (`launch.rs:117`, defaulted to `NoopAuditSink` at 159) but is never read anywhere in `eos-workflow/src` (grep finds only the field declaration + the default construction). `run_stage.rs` `mark_launch_failed` just sets task status with no `task_failed` emission (137-193), and the ready/launch path emits nothing (41-112).
Why it matters: an entire documented subsystem's behavior is dropped. It is pure observability today (no state-machine logic depends on it), so the harness still works — but the doc promises these events and Python produces them, so it is a clear parity gap, not an aside.
Suggested fix: wire `audit_sink` into `AttemptStageAdvancer` and emit `task_ready` before launch, `task_launched` after `set_task_status(Running)`, and `task_failed` in `mark_launch_failed`, matching the payload shapes in `workflow/_core/audit.py`. If audit was intentionally consolidated elsewhere (see OQ2), document that and downgrade.

## Extra findings

- E2 (generator membership tightened, benign): Rust `record_generator_submission` pre-checks `attempt.generator_task_ids.contains(&task_id)` (orchestrator.rs:497-503) AND re-checks belongs-to-attempt + role in `mark_execution_task` (552-564). Python `_mark_generator` only does `assert_generator_task_for_submission` (belongs-to-attempt + role==GENERATOR, invariants.py:131-134) with no `generator_task_ids` membership check. Rust is strictly stricter — not a gap. Reducers match Python (`_mark_reducer` checks `reducer_task_ids` membership first, orchestrator.py:196-200 == orchestrator.rs:524-530).
- E3 (`close_attempt` idempotency divergence, low): Python `_close_attempt` raises if the attempt is already closed or not running (`assert_attempt_not_closed` + status check, orchestrator.py:253-255). Rust `close_attempt` returns `Ok(())` early if `attempt.is_closed()` (orchestrator.rs:620-622) — idempotent rather than erroring. Reasonable given the JoinSet loop can reach close from multiple branches; behavioral, not a bug.
- E4 (extra-task_specs check present at tool layer): Python rejects `task_specs` with unknown ids (`_schemas.py:174-176`); Rust mirrors this in `validate_planner_structure` (submission.rs:487-493). MATCH — included here only because the orchestrator's `materialize_plan_tasks` itself does not re-check it (relies on the tool layer).
- E5 (registry register semantics match): both reject re-registering a *different* orchestrator for the same attempt and are idempotent for the same instance (orchestrator_registry.py:45-52 == orchestrator_registry.rs:32-45, using `Arc::ptr_eq`).

## Open questions

- OQ1 (D5 timeline): Is the Phase-7 wiring of `RuntimeAgentRunner` (typed-terminal capture + single drive path) in scope for this parity pass, or is the harness intentionally left runner-incomplete until a later milestone? This determines whether D5 is a "must-fix now" or "tracked gap."
- OQ2 (audit, D8): Are workflow audit events (`workflow.task.*`) intended to be ported into `eos-workflow`, or has audit been consolidated into another crate/layer (e.g. `eos-audit` engine stream) that observes task-store writes instead of explicit emission? If the latter, D8 may be satisfied elsewhere — not found in this area's files.
- OQ3 (D2 severity): Does any consumer rely on topo-ordered `generator_task_ids` beyond outcome projection (e.g. reducer context composition reading generator outcomes in order)? If the reducer's composed context depends on generator order, D2 rises toward medium.
