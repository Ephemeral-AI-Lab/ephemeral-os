# Phase 05 — Implementation Plan

Companion to
[`phase-05-workflows-and-cutover.md`](./phase-05-workflows-and-cutover.md).
This plan turns the Phase 05 design doc into concrete, ordered work.

**Out of scope for this phase (explicitly deferred):**
- Cold-restart resurrection of process-local orchestrators from durable rows.
- Registry hygiene under `start()` failure (Phase 04 known limitation).
- Frontend client work against `/api/db/task-center-runs/{id}/graph`.
- Phase 06 context-engine work (rich helper-agent context, evidence summaries,
  `harness_graph_summary_id` population).

---

## 1. Current state audit (already done by prior phases)

A grep sweep before drafting this plan confirms most cutover items in the
design doc are already executed:

| Design-doc item | Current state |
| --- | --- |
| `submit_request_plan` removed from executor terminals | Already gone — only negative asserts remain (`test_agent_markdown.py:36`, `test_submission_tool_registration.py:34`, `test_harness_graph_orchestrator.py:536`) |
| `RETRY_ON_FAILURE` graph spawn | No production references |
| `ROOT` spawn / creation reason | No production references |
| `retry_after_partial` | No references in `backend/src` |
| `plan_shape` persisted field | Only appears in `test_db_engine.py` legacy schema fixtures (lines 139, 196) — engine plumbing |
| Attempt-row child-graph retry | Replaced by `TaskSegmentManager` retry inside same segment (Phase 02) |
| `final_harness_graph_id` in `TaskSegmentClosureReport` | **Kept** — design doc explicitly notes this remains valid as event payload; the removal item is for *persisted* fields only |

**Implication:** Phase 05 is dominated by **end-to-end validation tests** plus
a small amount of **legacy-fixture cleanup** plus **resolving the two open
questions**, not a sweeping refactor.

---

## 2. Work breakdown

### 2a. Workflow validation tests (the bulk of Phase 05)

A new lifecycle test module per workflow path. All under
`backend/tests/task_center/lifecycle/phase05_workflows/`.

| File | Coverage |
| --- | --- |
| `test_phase05_full_plan_happy.py` | Single segment, single graph, planner submits `submit_full_plan` → generators succeed → evaluator success → `terminal_success` → request closes → close report delivered to requesting executor |
| `test_phase05_partial_continuation.py` | S1.H1 planner submits `submit_partial_plan` with `continuation_goal=G` → S1 closes `success_continue(G)` → S2 created with `goal=G` → S2.H1 planner is **gated** to `submit_full_plan` (recursive partial gate) → S2 closes `terminal_success` → only one final close report delivered |
| `test_phase05_segment_retry_then_pass.py` | S1.H1 fails → `TaskSegmentManager` creates S1.H2 in same segment; H2's `continuation_goal` is independent of H1; H2 passes; segment closes with `final_harness_graph_id = H2.id`; `attempted_plan_history` contains H1 |
| `test_phase05_failure_paths.py` | Three sub-tests: generator failure (dependents BLOCKED, independents continue, then quiescent → `generator_failed`), evaluator failure (`evaluator_failed`), planner exhaustion (`planner_failed`). Each runs both budget-remaining (creates next graph) and budget-exhausted (`attempt_plan_failed` → request closes failed) branches |
| `test_phase05_resolver_loop.py` | Inside a single graph, `ask_resolver` increments unresolved counter; at 5 unresolved calls, success terminals are blocked and caller must submit failure |
| `test_phase05_recursive_partial_gate.py` | A continuation segment's planner is rejected if it tries to `submit_partial_plan`; only `submit_full_plan` is allowed in S_{n>1} |
| `test_phase05_delegated_request_inside_graph.py` | A generator executor inside an in-flight harness graph calls `request_complex_task_solution` → creates a child `ComplexTaskRequest` linked via `requested_by_task_id`; parent generator transitions to `waiting_complex_task`; on close, parent receives close report (this exercises the Phase 04 handoff coordinator under realistic in-graph conditions) |
| `test_phase05_no_legacy_artifacts.py` | Static asserts: no `submit_request_plan` tool registered, no `RETRY_ON_FAILURE` constant exposed, no `ROOT` spawn reason, no `retry_after_partial` symbol, no `plan_shape` column on the live SQLAlchemy models. Acts as a regression net |

**Target test count:** ~35–45 new tests across these files.

**Fixtures:** Reuse the existing `request_store / segment_store / graph_store /
task_store / task_center_run_id` lifecycle fixtures plus the
`_build_orchestrator` helper from `test_harness_graph_orchestrator.py`. Where
end-to-end coverage requires real planner/generator/evaluator submissions,
reuse the submission test helpers from `backend/tests/test_tools/`
(`make_tool_context`, etc.).

### 2b. Legacy-fixture cleanup

| Item | Action | File |
| --- | --- | --- |
| `plan_shape VARCHAR(16)` in legacy schema fixture | If the fixture is for a historical-DB-engine smoke test that still must accept old DBs, **leave it** with a comment pointing to the migration; if it's only modeling current schema, drop the column | `backend/tests/test_config/test_db_engine.py:139,196` |
| Negative-assert tests | Keep as-is — they document removed surface | n/a |

The decision in row 1 needs a quick read of `test_db_engine.py`'s purpose
before deciding. Default: leave it (it's a regression net for legacy DB
files, not active schema).

### 2c. Resolved design questions (no new code required)

Both Phase 05 open questions are already answered by the existing runtime.
The work is documentation + test coverage, not new machinery.

**Q1: Retry budget lives on `TaskSegment`, not `ComplexTaskRequest`.**

`ComplexTaskRequest` does not retry — only `TaskSegment` does, by creating
the next `HarnessGraph` inside itself. The budget is a fixed runtime
default applied at segment creation:

- `HarnessLifecycleConfig.default_attempt_budget = 2`
  (`backend/src/task_center/config.py:16`)
- `ComplexTaskRequestHandler` consumes it at `handler.py:98,122` for both
  initial and continuation segments.
- `TaskSegment.attempt_budget` and `has_budget_remaining` drive
  `TaskSegmentManager`'s retry decision.

**Action:** none. No per-request override is added. The design doc's
"Resolved design questions" section now records this decision.

**Q2: Planner-exhaustion signal.**

Planner exhaustion = planner agent terminates without a valid
`submit_full_plan`/`submit_partial_plan`. The runtime dispatches a
`PlannerFailureSubmission`; `HarnessGraphOrchestrator.apply_planner_failure`
(`orchestrator.py:162–190`) closes the graph with
`HarnessGraphFailReason.PLANNER_FAILED`. Already implemented.

**Action:** add a single end-to-end test in `test_phase05_failure_paths.py`
that drives a planner agent which exits without submission and asserts the
graph closes with `PLANNER_FAILED` and the `TaskSegmentManager` decision
follows the same retry-or-fail path as the other failure modes.

### 2d. Doc/prompt sweep

A short pass to remove any prompt or doc reference to:
- "retry as a child graph" / `RETRY_ON_FAILURE`
- `submit_request_plan`
- `retry_after_partial`

Files to check (grep with focus): `backend/src/agents/**/*.md`,
`backend/src/tools/submission/**/prompts.py` (if any),
`docs/architecture/**/*.md` outside the migration folder. If grep is clean,
mark this row done with no edits.

---

## 3. Execution order

Q1 and Q2 are resolved in §2c with no code change required, so test work
can start immediately.

1. **Validation test files** in the order of §2a (simplest first: full-plan
   happy path → partial continuation → retry → failure paths → resolver loop
   → recursive gate → delegated-in-graph → legacy regression net).
2. **Doc/prompt sweep** (§2d).
3. **Legacy-fixture decision** (§2b row 1).
4. **Final verification:** full focused suite + ruff + strict mypy.

Each step lands as its own commit. Order matches the design doc's "Cutover
sequence" section §11 ("Run targeted TaskCenter runtime tests, then broader
backend checks") — but the heavy lift in §1–§10 is already done by Phases
01–04, so this plan compresses that into the test-plus-residual work above.

---

## 4. Verification

Per the Phase 05 design doc and matching Phase 04's verification style:

```bash
uv run pytest backend/tests/task_center/lifecycle/phase05_workflows -q
uv run pytest backend/tests/task_center -q
uv run pytest backend/tests/test_tools backend/tests/task_center backend/tests/server -q
uv run ruff check backend/src backend/tests
uv run mypy --config-file backend/mypy.ini backend/src/task_center backend/src/agents
```

All must be green.

---

## 5. Exit-criteria mapping

| Exit criterion (from design doc) | Coverage |
| --- | --- |
| All phase tests pass | Verification commands above |
| Public executor contract = `request_complex_task_solution` + `submit_execution_{success,failure}` | `test_phase05_no_legacy_artifacts.py` |
| Docs no longer describe retry as `RETRY_ON_FAILURE` child graph creation | 2d sweep + the design doc itself |
| Segment progression reflects continuation through `continuation_goal` inherited from passing harness graph | `test_phase05_partial_continuation.py` |
| Retry history derived from ordered harness graphs inside one segment, with per-graph `continuation_goal` independence | `test_phase05_segment_retry_then_pass.py` |

---

## 6. Risk register

| Risk | Mitigation |
| --- | --- |
| Validation tests reveal hidden coupling that needs runtime fixes | Each test file is independent; if one surfaces a runtime bug, fix-then-test in that file's commit, don't bundle |
| Retry-budget default change destabilizes Phase 04 tests that hard-coded values | Add the override knob *before* changing the default; Phase 04 tests use stub managers anyway |
| `plan_shape` legacy fixture turns out to gate a real migration test | Read the fixture's intent before touching; default to leaving it |
| Doc/prompt sweep pulls a thread (a prompt depends on a phrase that's now wrong) | Keep edits minimal; if a prompt rewrite is non-trivial, scope it as a follow-up note in the implementation report rather than expanding Phase 05 |

---

## 7. Definition of done

- All eight test modules in §2a exist and are green.
- Q1 and Q2 are resolved with code changes + one test each.
- No `RETRY_ON_FAILURE`, `submit_request_plan`, `retry_after_partial`,
  `ROOT` spawn reason, or `plan_shape` references survive in `backend/src`
  or in active test fixtures (legacy DB-engine fixture decision documented
  in the implementation report).
- Verification commands in §4 are clean.
- An implementation report exists at
  `docs/architecture/task-center-harness-migration/phase-05-implementation-report.md`
  matching the structure of Phase 04's report (verdict, file inventory,
  LOC, test outcome, exit-criteria mapping, deferred items).
