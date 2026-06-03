# Verification — Attempt Harness (agent-core)

Independent re-derivation. Python = ground truth; Rust under `agent-core/crates/eos-workflow/src/attempt/` + boundary (`eos-tools/src/model_tools/submission.rs`, `eos-workflow/src/ports.rs`, `eos-runtime/src/agent_runner.rs`). Every file the investigation cites was reopened; constants were extracted on both sides.

> **Provenance note.** A prior `.verify.md` existed at this path asserting the investigation `attempt_harness.md` "was never written" and concluding a clean 11x confirmed_match with only two low-sev disparities (its F2=topo order, F3=concurrency cap). That premise is **false now**: `attempt_harness.md` is present and substantive (116 lines, disparities D1–D8). The prior pass therefore never engaged D5/D6/D8 and would have let the high-severity production-drivability gap (D5) pass as clean. This file supersedes it. The prior pass's *independent* agreement on invariants 1–5, the constant set, and the existence of the topo-order + cap disparities is recorded below as corroboration.

## Invariant verdict table

| # | Invariant | independent_status | severity | decisive bilateral anchor |
|---|-----------|--------------------|----------|---------------------------|
| 1 | Each Attempt owns ONE planner-authored DAG of generator + reducer Task rows whose edges are `needs` | confirmed_match (one low-sev residual) | low | Combined DAG built+validated `orchestrator.rs:184-384` (lane-shape 318-373, dangling 374-382, acyclic 742-797) vs `plan_dag.py:34-122`; rows persisted with resolved `needs`+`attempt_id` `orchestrator.rs:235-294` vs `_schemas.py:222-249`. Residual = reducer↔reducer dup only (D1). |
| 2 | Attempt stages PLAN -> RUN -> CLOSED | confirmed_match | — | `eos-state/src/attempt.rs:17-23` `Plan/Run/Closed` (no GENERATE/EVALUATE) vs `state.py:120-123`; set Run `orchestrator.rs:438` vs `orchestrator.py:138`; RUN no-op for Plan/Closed `run_stage.rs:51` vs `run_stage.py:60`. |
| 3 | Reducer is the EXIT GATE; attempt closes through the reducer | confirmed_match | — | Close driven by `dag_status` quiescence `run_stage.rs:79-96` → `close_attempt` (599-644) vs `run_stage.py:80-89` → `_close_attempt`; reducer rows materialized `orchestrator.rs:262-295`, included in `plan_task_records` (646-662). Tests `reducer_is_exit_gate`/`failed_reducer_closes_attempt_failed` (orchestrator.rs:819-921). |
| 4 | Generators + reducers launched per planned tasks, respecting `needs` edges | confirmed_match (selection); confirmed_disparity (production drivability) | high (D5) | Needs-respecting readiness identical: `ready_pending_plan_ids` `plan_dag.rs:19-32` (`*s == Done`) vs `plan_dag.py:158-169` (`== DONE`). RUN launch `run_stage.rs:55-75` vs `run_stage.py:69-78`. BUT production runner never yields a terminal (`agent_runner.rs:104`) — D5. Plus per-attempt cap (D3) + drive-model split (D4). |
| 5 | AttemptOrchestrator is per-Attempt, not a global layer | confirmed_match | — | `orchestrator_registry.rs:13-14` `Mutex<HashMap<AttemptId, Arc<AttemptOrchestrator>>>` keyed by attempt id vs `orchestrator_registry.py:39-67`; one attempt per orchestrator `orchestrator.rs:31-58` vs `orchestrator.py:50-72`. Process-local; no global orchestrator (grep: no singleton). |

### Constant/operator parity (re-extracted — all MATCH)
- `is_terminal_generator = matches!(Done|Failed|Blocked)` (`eos-state/src/task.rs:34-35`) == `TERMINAL_GENERATOR_STATUSES = {DONE,FAILED,BLOCKED}` (`task.py:29-35`).
- Failure set `matches!(Failed|Blocked)` (`plan_dag.rs:46,126`) == `_FAILED_OR_BLOCKED = (FAILED,BLOCKED)` (`plan_dag.py:179`).
- Readiness uses `==` Done (not `>=`) on both sides.
- Close validity (failed requires fail_reason / passed forbids it) `orchestrator.rs:604-613` == `assert_valid_attempt_close` (`orchestrator.py:251`).
- `AttemptFailReason = TaskFailed + StartupFailed` only (`eos-state/src/attempt.rs:42-46`).
- `PlannerFailReason = RunExhausted` only; planner failure closes `Failed/TaskFailed` (`orchestrator.rs:446-467` vs `orchestrator.py:141-150`).
- `dag_status` quiescence/done/failed + `unreachable_pending_ids` DFS-with-cycle-detection identical (`plan_dag.rs:34-138` vs `plan_dag.py:194-255`).

## Disparity adjudication

**D1 — combined dup-id check / DAG corruption — ADJUSTED (headline is a FALSE ALARM; narrow residual kept).**
The investigation's headline — a colliding generator id and reducer id "simply coexist … silently produces a malformed plan (duplicate persisted id, lost generator row, double counting)" — is **refuted**. I traced a concrete colliding plan (gen `x` + reducer `x`) through `validate_plan_shape`, which runs BEFORE `materialize_plan_tasks`:
  - For gen `x` to survive the dangling check (`orchestrator.rs:374-382`, keyed over `generator_ids` ∋ `x`), some task must `need` x.
  - A *generator* needing `x` trips `orchestrator.rs:318-330` (`x ∈ reducer_ids` → "generator task cannot need reducer task(s)").
  - A *reducer* needing `x` trips `orchestrator.rs:356-358` (same).
  - So nothing can need `x` without erroring ⇒ `x` is dangling ⇒ error. Every construction errors out.
  The gen/red collision IS rejected — just with a different message than Python's "duplicate local ids". No malformed plan reaches `materialize_plan_tasks`; the lost-row/double-count scenario cannot occur.
**Real residual (kept, LOW):** reducer↔reducer duplicate. `reducer_ids`/`by_needs` are `BTreeSet`/`BTreeMap` (collapse dups), so two reducers sharing id `r1` pass `validate_plan_shape` + `assert_acyclic`; then `materialize_plan_tasks` iterates the **Vec** `for reducer in &plan.reducers` (`orchestrator.rs:262`) and pushes `red:r1` into `reducer_task_ids` twice. Python's `ordered_plan_tasks` rejects via the combined `(*generators,*reducers)` loop (`plan_dag.py:47-55`). Generator↔generator dup is caught at the tool layer (`submission.rs:472-478`). Net: real but narrow LOW gap (duplicated reducer id in one vector), not the medium silent corruption claimed. Severity medium → low; mechanism corrected.

**D2 — topo ordering dropped — CONFIRMED (low).**
Python persists ids in Kahn topo order (`plan_dag.py:70-74`, consumed `_schemas.py:222-257`); `project_attempt_outcomes` iterates `(*generator_task_ids,*reducer_task_ids)` in stored order (`outcomes.py:88`). Rust `materialize_plan_tasks` persists raw plan order (`orchestrator.rs:207-305`); `assert_acyclic` computes order only to detect cycles, then discards it (`orchestrator.rs:773-795`). Model-visible outcome ordering diverges; dispatch needs-driven and unaffected. (Prior verify independently found this as F2 — corroborated.)

**D3 — `max_concurrent_task_runs` cap — CONFIRMED (low, intentional/additive).**
Rust caps fan-out at `deps.max_concurrent_task_runs` (default 8, `launch.rs:160`; enforced `run_stage.rs:43-47,56-57`; `==0` guarded `orchestrator.rs:732-739`). Python launches every ready task per pass (`run_stage.py:69-78`). Behavioral but additive back-pressure; breaks no invariant. Test `fanout_respects_concurrency_cap` (run_stage.rs:441-483). (Prior verify independently found this as F3 — corroborated.)

**D4 — drive model (store-driven re-entrant vs owning JoinSet loop) — CONFIRMED (low-medium, latent).**
Python: terminal submission → `advance_ready_tasks`; fire-and-forget asyncio tasks call back into advancing variants (`orchestrator.py:152-160`, `launch.py:157-181`). Rust: `advance_run_stage` owns a `JoinSet`, applies returns via non-advancing `record_*_submission`, loops itself (`run_stage.rs:49-111,222-241`); public advancing variants spin a fresh advancer (`orchestrator.rs:470-489`). Two terminal-feed paths exist (return-value loop + tool-port `PlanSubmissionAdapter`, `ports.rs:59-83`). Latent double-apply risk; today benign because D5 makes the production runner never return a terminal, so only the tool-port path is live. Confirmed.

**D5 — production `RuntimeAgentRunner` never yields a terminal — CONFIRMED (high; documented Phase-7).**
Independently confirmed only three `AgentRunner` impls exist: `QueueRunner` + `ScriptedRunner` (test doubles, `testsupport.rs:549,652`) and `RuntimeAgentRunner` (`agent_runner.rs:52`), the latter wired into production at `entry.rs:130`. It runs with `plan_submission = None` and unconditionally returns `AgentRunReport::no_terminal(...)` (`agent_runner.rs:104`). Consequence: every production planner/generator/reducer run is treated as exhaustion → `run_exhausted` → attempt closes FAILED before any plan executes. The harness logic (invariants 3+4) is correct in unit tests but unreachable in the live runtime. Highest functional parity gap in this area; flagged in the module doc (`agent_runner.rs:1-13`). Confirmed. (The prior verify MISSED this entirely — its clean "confirmed_match" on invariant 4 is unsafe.)

**D6 — generator-capability role gate dropped — CONFIRMED (low-medium).**
Python rejects a planner generator task whose `agent_name` is not a GENERATOR-role profile: `_is_generator_capable_agent` requires `definition.role == AgentRole.GENERATOR` (`_schemas.py:136-167`). Rust has NO role check: `validate_planner_structure` (`submission.rs:471-495`) checks only dup ids + spec coverage, and `materialize_plan_tasks` does existence-only `agent_registry.get(&agent_name)` (`orchestrator.rs:224-229`). A planner could bind a generator slot to a planner/reducer/helper profile and pass. Re-verified: no implicit role check anywhere on the generator path. Reducer slot is hardcoded to `"reducer"` (`orchestrator.rs:256,288`), so the gap is generator-side only — same shape as Python. Confirmed. (The prior verify MISSED this.)

**D7 — no `_fail_unowned_attempt` path — CONFIRMED (low, structurally moot).**
Python's launcher fail-safe-closes an attempt whose orchestrator is missing at exhaustion time (`launch.py:284-347`: `_report_exhaustion` does `orchestrator_registry.get` at `:343`, falls back to `_fail_unowned_attempt`). Rust exhaustion runs from inside `apply_report`/`synthesize_failure` holding the `Arc<AttemptOrchestrator>` (`run_stage.rs:195-288`), so "missing orchestrator" can't occur on the return-value path; the tool-port path returns `Rejected("attempt ... is not active")` without closing/notifying (`ports.rs:50-82`). Artifact of the drive model; low.

**D8 — workflow-audit subsystem (`workflow.task.ready|launched|failed`) absent — CONFIRMED (medium, observability-only).**
Confirmed: `AttemptDeps.audit_sink` is declared (`launch.rs:117`) and defaulted to `NoopAuditSink` (`launch.rs:159`) but never read — grep across `eos-workflow/src` finds only the declaration + default, and `grep -rn "workflow\.task\."` across all crates returns nothing. Python emits `task_ready`/`task_launched`/`task_failed` via `WorkflowAuditEmitter` (`run_stage.py:50,145-149,153,124-129`). Rust `mark_launch_failed` (137-193) and the ready/launch path (55-75) emit nothing. Pure observability today; documented subsystem dropped. Confirmed medium. (The prior verify MISSED this.)

### Extra findings adjudication (E2–E5)
- **E2 (generator membership tightened) — CONFIRMED benign.** Rust `record_generator_submission` pre-checks `generator_task_ids.contains` (`orchestrator.rs:497-503`) AND re-checks belongs/role in `mark_execution_task` (552-564); Python `_mark_generator` only does `assert_generator_task_for_submission` (`orchestrator.py:184`). Rust strictly stricter; reducers symmetric. Not a gap.
- **E3 (`close_attempt` idempotency) — CONFIRMED.** Rust returns `Ok(())` if already closed (`orchestrator.rs:620-622`); Python raises (`assert_attempt_not_closed`, `orchestrator.py:253`). Reasonable given the JoinSet loop reaching close from multiple branches. Low.
- **E4 (extra/missing task_specs) — CONFIRMED MATCH.** `submission.rs:483-493` mirrors `_schemas.py:171-176`.
- **E5 (registry register semantics) — CONFIRMED MATCH.** Both reject re-registering a *different* orchestrator and are idempotent for the same instance (`orchestrator_registry.rs:32-45` via `Arc::ptr_eq` == `orchestrator_registry.py:45-52` via `is`).

## New findings

- **NF1 (D1 correction):** the investigation's D1 headline is a FALSE ALARM on the gen/red-collision mechanism (caught by lane-shape + dangling checks); only reducer↔reducer dup survives, at low severity. Single substantive correction to the investigation.
- **NF2 (registry primitive divergence, informational):** Rust uses `parking_lot::Mutex<HashMap>` (`orchestrator_registry.rs:14`) — genuinely concurrency-safe — vs Python's bare `dict` under single-threaded async. Strengthens invariant 5; not a global-orchestration concern. Recorded so a future reviewer doesn't mistake the lock for added orchestration state.
- **NF3 (no investigator_missed / no FALSE MATCH in the harness logic):** I hunted invariants 3 and 4 for claimed-match-but-broken. Closure logic, quiescence set, and readiness operator are exact; the only thing that breaks invariant 4 in production is D5, which the investigation already flagged loudly as high. No mislabeled match in the *investigation*.
- **NF4 (prior verify pass was unsafe — orchestration hazard):** the superseded `.verify.md` ran on the false premise that no investigation existed and returned a clean all-match with D5/D6/D8 absent. That document, if trusted, would let a high-severity gap ship as parity-complete. Recommend the orchestrator discard it in favor of this file.

## Overall verdict

The investigation is substantially sound. All five invariants are correctly characterized at the harness-logic level; the state machine (PLAN→RUN→CLOSED), reducer exit gate, needs-driven dispatch, per-attempt registry, and the full constant/operator set are faithful Rust ports. The dominant real gap is **D5** (production `RuntimeAgentRunner` cannot drive the harness to completion — high, but a documented Phase-7 stub, not a silent bug), corroborated by **D8** (audit subsystem dropped — medium, observability-only) and **D6** (generator role gate dropped — low-medium, a genuine validation hole). One correction: **D1 is overstated** — the headline gen/red-id-collision corruption is actually rejected by the lane-shape + dangling checks (FALSE ALARM); only a reducer↔reducer duplicate slips through (low). No false-match (claimed-match-but-broken) was found in the investigation. Independent of the investigation, a prior verify pass agreed on invariants 1–5 + constants and re-found D2/D3, raising confidence on those verdicts.
