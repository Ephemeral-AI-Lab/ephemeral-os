# Phase 02 - Implementation Report

Companion to
[`phase-02-implementation-plan.md`](./phase-02-implementation-plan.md) and
[`phase-02-harness-graph-orchestrator-lifecycle.md`](./phase-02-harness-graph-orchestrator-lifecycle.md).
This report records what was actually delivered, the line-count accounting,
the verified runtime workflow, and the remaining migration seams.

---

## 1. File inventory

### Domain task primitives (Wave 1)

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/task_center/task/models.py` | new | `HarnessTaskRole`, `HarnessTaskStatus`, terminal generator statuses, and typed planner/generator/evaluator submission DTOs |
| `backend/src/task_center/task/ids.py` | new | Stable planner, generator, and evaluator task id helpers |
| `backend/src/task_center/task/__init__.py` | edited | Re-export task models and id helpers from the split modules |

### Persistence helpers (Wave 1)

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/db/stores/task_center_store.py` | edited | Graph-scoped task lookup, generator-task listing, single-task fetch, and status plus summary updates |

### DAG and orchestration runtime (Waves 2-4)

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/task_center/harness_graph/task_graph.py` | new | Generator DAG validation, topological ordering, dependency readiness, descendant blocking, and quiescence helpers |
| `backend/src/task_center/harness_graph/runtime.py` | new | `HarnessGraphRuntime`, `HarnessAgentLaunch`, and `HarnessAgentLauncher` protocol |
| `backend/src/task_center/harness_graph/orchestrator_registry.py` | new | Process-local active orchestrator lookup by `HarnessGraph.id` |
| `backend/src/task_center/harness_graph/orchestrator.py` | edited | Full planner -> generator DAG -> evaluator state machine |
| `backend/src/task_center/harness_graph/factory.py` | new | Composition helper that registers and returns graph orchestrators |
| `backend/src/task_center/harness_graph/__init__.py` | edited | Lazy package exports for Phase 02 lifecycle helpers without circular imports |

### Handler and manager wiring (Wave 5)

| File | Status | Purpose |
| --- | --- | --- |
| `backend/src/task_center/segment/manager.py` | edited | Start a configured orchestrator after graph creation and keep retry ownership inside the segment manager |
| `backend/src/task_center/complex_task/handler.py` | edited | Accept and pass an orchestrator factory to every spawned segment manager |

### Architecture docs

| File | Status | Purpose |
| --- | --- | --- |
| `docs/architecture/task-center-harness-migration/phase-02-implementation-plan.md` | edited | Synced the delivered task module layout and actual test names after review |

### Tests

| File | LoC | Purpose |
| --- | ---: | --- |
| `backend/tests/task_center/persistence/test_task_center_task_helpers.py` | 65 | Store helper coverage |
| `backend/tests/task_center/lifecycle/test_harness_graph_task_graph.py` | 86 | Generator DAG and quiescence helpers |
| `backend/tests/task_center/lifecycle/test_harness_graph_orchestrator_registry.py` | 35 | Process-local registry behavior |
| `backend/tests/task_center/lifecycle/test_harness_graph_orchestrator.py` | 476 | Graph-scoped orchestration state machine |
| `backend/tests/task_center/lifecycle/test_task_segment_manager.py` | 318 | Manager factory seam and retry graph startup |
| `backend/tests/task_center/lifecycle/test_complex_task_request_handler.py` | 346 | Handler factory propagation and request/segment behavior |
| `backend/tests/task_center/lifecycle/test_integration_phase02.py` | 190 | Handler -> manager -> orchestrator integration smoke |

---

## 2. Lines of code

Current line counts for Phase 02 implementation files:

| Bucket | Files | Lines |
| --- | ---: | ---: |
| Task DTOs and id helpers | 3 | 133 |
| Harness graph runtime, registry, DAG helpers, factory, exports | 5 | 397 |
| Harness graph orchestrator | 1 | 476 |
| Handler and manager lifecycle wiring | 2 | 452 |
| TaskCenterStore task helper surface | 1 | 262 |
| Phase 02 tests listed above | 7 | 1,516 |
| **Total participating files** | **19** | **3,236** |

The task primitive split from `task.py` / `task_ids.py` to
`models.py` / `ids.py` was completed after review and reflected in the plan.

---

## 3. Test outcome

Commands run after the review fixes:

- `uv run pytest backend/tests/task_center -q`: **104 passed**
- `uv run ruff check backend/src/task_center backend/tests/task_center docs/architecture/task-center-harness-migration/phase-02-implementation-plan.md`: clean
- `uv run mypy --config-file backend/mypy.ini backend/src/task_center`: clean
- Package export smoke:
  `from task_center.harness_graph import HarnessGraph, HarnessGraphOrchestratorRegistry, make_harness_graph_orchestrator_factory`: clean
- `git diff --check`: clean
- `graphify update .`: graph rebuilt (`5292 nodes`, `16004 edges`)

Exit criteria mapping:

| Exit criterion | Test |
| --- | --- |
| Harness graph can complete a full-plan execution successfully | `test_full_plan_execution_success_closes_request_success` |
| Planner exhaustion closes graph with `planner_failed` | `test_apply_planner_failure_marks_task_and_closes_graph` |
| Planner submission creates generator task records and launches roots | `test_apply_plan_submission_persists_contract_and_generator_ids` |
| Full and partial planner submissions share one apply path | `test_apply_partial_plan_submission_stores_continuation_goal` |
| Generator success launches newly ready dependents | `test_apply_generator_success_launches_newly_ready_dependents` |
| Missing generator launch metadata is not silently downgraded | `test_missing_generator_agent_profile_is_invariant_violation` |
| Generator failure blocks pending descendants | `test_apply_generator_failure_blocks_pending_descendants` |
| Generator failure waits for independent running work before closing | `test_generator_failure_waits_then_closes_after_quiescence` |
| `waiting_complex_task` is non-terminal and keeps graph in `generating` | `test_waiting_complex_task_prevents_generator_quiescence` and `test_waiting_complex_task_is_not_quiescent_or_done` |
| All generators done spawns evaluator | `test_all_generators_done_spawns_evaluator` |
| Evaluator success closes graph passed | `test_apply_evaluator_success_closes_graph_passed` |
| Evaluator failure closes graph failed immediately | `test_apply_evaluator_failure_closes_graph_failed` |
| Orchestrator never creates retry graphs | `test_orchestrator_never_creates_retry_graph` |
| Retry remains delegated to `TaskSegmentManager` | `test_failed_graph_with_budget_starts_next_graph_orchestrator` |
| Handler passes orchestrator factory to spawned managers | `test_handler_passes_orchestrator_factory_to_spawned_manager` |

---

## 4. Runtime workflow now implemented

Phase 02 fills one graph execution:

```text
ComplexTaskRequestHandler
  create_initial_segment(...)
  register TaskSegmentManager(S1, orchestrator_factory)
        |
        v
TaskSegmentManager.create_initial_harness_graph()
  insert HarnessGraph H1
  append H1 to S1.harness_graph_ids
  factory(H1, handle_harness_graph_closed)
  orchestrator.start()
        |
        v
HarnessGraphOrchestrator.start()
  create H1:planner task row as running
  graph.planner_task_id = H1:planner
  launch planner
        |
        v
Planner terminal success
  apply_plan_submission(...)
  mark planner done
  persist graph contract
  create all generator task rows as pending
  graph.stage = generating
  launch dependency-free generators as running
        |
        v
Generator terminal submissions
  apply_generator_submission(...)
  success -> mark generator done -> launch newly ready dependents
  failure -> mark generator failed -> block pending descendants
  waiting_complex_task rows stay non-terminal
        |
        v
Generator quiescence
  all done -> spawn evaluator and set stage evaluating
  any failed/blocked -> close graph failed(generator_failed)
        |
        v
Evaluator terminal submission
  apply_evaluator_submission(...)
  success -> close graph passed
  failure -> close graph failed(evaluator_failed)
        |
        v
_close_graph(...)
  graph_store.close(...)
  deregister orchestrator
  on_graph_closed(H1)
        |
        v
TaskSegmentManager.handle_harness_graph_closed(H1)
  passed -> close segment as success or success_continue
  failed + budget -> create next HarnessGraph in same segment
  failed + no budget -> close segment with AttemptPlanFailed
```

The orchestrator still owns only one `HarnessGraph`. It never creates
`ComplexTaskRequest`, `TaskSegment`, or sibling graph rows. Segment retry
policy remains inside `TaskSegmentManager`.

---

## 5. State invariants enforced now

- `start()` only runs for a running graph in `planning` with no existing
  planner task.
- Planner success must reference the graph's current planner task.
- Full plans cannot carry `continuation_goal`; partial plans must carry it.
- Generator plan local ids cannot duplicate, dangle, or form cycles.
- Generator task deps are persisted as stable graph-scoped task ids.
- Generator submissions only apply in `generating` and only to running
  generator tasks owned by the current graph.
- A running descendant of a failed generator is a graph invariant violation.
- Pending descendants of a failed generator become `blocked`.
- `waiting_complex_task` is excluded from terminal generator statuses.
- Ready generator dispatch requires registered generator agent profile metadata;
  missing metadata raises `GraphInvariantViolation` instead of falling back to a
  generic `generator` profile.
- Evaluator submissions only apply in `evaluating` and only to the graph's
  evaluator task.
- Graph close is centralized through `_close_graph(...)`.
- Failed graph closes require a fail reason; passed graph closes cannot include
  one.
- Closing a graph deregisters the process-local orchestrator before notifying
  the segment manager.

---

## 6. Review follow-up applied

The implementation review produced three findings; all were addressed:

| Finding | Resolution |
| --- | --- |
| Missing generator agent profile silently fell back to `generator` | `_dispatch_generating` now raises `GraphInvariantViolation`; regression test confirms the ready dependent stays pending |
| `task_center.harness_graph` package exports were missing | Added lazy `__getattr__` exports for Phase 02 helpers and an import smoke check |
| Phase 02 implementation plan referenced stale files and nonexistent tests | Updated folder layout, wave tasks, test plan, command list, and exit-criteria mapping |

---

## 7. What's deferred

| Item | Where | Phase |
| --- | --- | --- |
| Public terminal tool schemas, role gates, and user-facing errors | `backend/src/tools/submission/main_agent/...` stubs | 03 |
| Rejection or aliasing of legacy `submit_request_plan` before orchestration | generator executor tool layer | 03 / 04 |
| `request_complex_task_solution` nested request spawn | complex-task request boundary | 04 |
| `HarnessGraphOrchestrator.apply_complex_task_close_report` resume path | `harness_graph/orchestrator.py` | 04 |
| Durable final close-report delivery to `requested_by_task_id` | `ComplexTaskRequestHandler.deliver_close_report` wiring | 04 |
| Durable orchestrator recovery after process restart | registry and cutover runtime | 05 |
| End-to-end replacement of old TaskCenter runtime paths | server/benchmark/runtime callers | 05 |
| Context-engine launch packets and durable graph summaries | planner/generator/evaluator launch + close payloads | 06 |
| `failure_landscape` and `harness_graph_summary_id` population | segment close reports | 06 |
