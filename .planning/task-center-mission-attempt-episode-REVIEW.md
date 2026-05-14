# Review: `task_center/{mission,attempt,episode}`

**Scope:** 12 files, 3,247 LOC
**Date:** 2026-05-15
**Focus:** naming, implementation quality, simplicity (LOC reduction), import depth ≤ 3

---

## Executive Summary

This is a well-factored lifecycle subsystem with a clear three-tier story (`mission → episode → attempt`) and a real adversarial story (CAS guards, idempotent re-delivery, compensation saga). The defects are not bugs — they are **shape problems**. Four of them stand out:

1. **`episode/__init__.py` is a 355-LOC module masquerading as a package init.** It holds `EpisodeManager` + `EpisodeManagerRegistry` + module re-exports. Compare to `attempt/__init__.py` (15-LOC shim) and `mission/__init__.py` (empty). The naming/layout asymmetry across three sibling packages is the single biggest readability cost in this subsystem.
2. **`mission/handler.py` (421 LOC) is a four-class grab-bag** (`MissionRepository`, `EpisodeFactory`, `EpisodeClosureRouter`, `MissionHandler`) plus a free function (`nested_mission_depth`). The filename "handler" tells you none of that. The docstring openly admits it absorbed two previously-separate files. Two of the four classes (`MissionRepository`, `EpisodeFactory`) are 1-line-forwarder layers.
3. **The starter / launch / orchestrator trio carry significant helper bloat.** `attempt/launch.py` (468 LOC) has a five-function tangle (`_mark_unowned_task_exhausted` / `_fail_unowned_attempt` / `_require_attempt_orchestrator` / `_report_exhaustion` / `_report_unfinished_running_task`) for one concern: "the agent run exited while the task was still RUNNING — synthesize a terminal failure." `mission/starter.py` (372 LOC) wraps a 20-line happy path in 350 LOC of compensation scaffolding.
4. **`attempt/contexts.py` ships four Protocols (`AttemptStageCtx`, `EpisodeLifecycleCtx`, `MissionLifecycleCtx`, `TaskCenterStores`) that production code never declares as parameter types.** Only `LaunchCtx` is structurally used. The rest are aspirational — kept alive by one test (`test_contexts_protocol_collapse.py`) that pins their existence.

**Net achievable LOC reduction without losing functionality: ~3,247 → ~2,070 (≈36%).** No import-depth violations — all 12 files import at depth ≤ 3.

---

## LOC Reduction Targets (criterion 2 — headline)

| File | Current | Target | Savings | Justification |
|---|---:|---:|---:|---|
| `mission/__init__.py` | 0 | 0 | 0 | Empty; fine. |
| `mission/close_report_router.py` | 72 | 60 | -12 | Dataclass-style `str(... or "") or None` simplification + collapse status branches. |
| `mission/handler.py` | 421 | 220 | -201 | Inline `MissionRepository`, `EpisodeFactory._spawn_manager`, delete `_orchestrator_factory` property/setter, drop pass-through methods on `MissionHandler`, move `nested_mission_depth` to its own file or `_core`. |
| `mission/starter.py` | 372 | 200 | -172 | Inline `_default_orchestrator_factory`, `_build_handler`, collapse `_close_unstarted_attempt_after_failed_start`, decide whether `_deliver_synthetic_failure_closure_report` is justified or scaffolding. |
| `mission/state.py` | 67 | 67 | 0 | Pure DTOs. Clean. |
| `attempt/__init__.py` | 15 | 15 | 0 | Already a thin re-export. |
| `attempt/contexts.py` | 124 | 50 | -74 | Delete unused Protocols (`AttemptStageCtx`, `EpisodeLifecycleCtx`, `MissionLifecycleCtx`) — only `LaunchCtx` and `TaskCenterStores` have real users. |
| `attempt/dispatcher.py` | 326 | 220 | -106 | Inline `_STAGE_DISPATCH` (2-entry table), merge generator + evaluator launch-failure cleanup, collapse `_fail_evaluator_spawn`. |
| `attempt/generator_dag.py` | 150 | 110 | -40 | Three boolean helpers each rebuild `generator_status_map` and iterate independently; replace with one `summarize_generator_states` and branch at the call site. |
| `attempt/launch.py` | 468 | 280 | -188 | Collapse the 5-function exhaustion-reporter graph into one function. Delete `_fail_reason_for_role` indirection. |
| `attempt/orchestrator_registry.py` | 47 | 47 | 0 | Tight. |
| `attempt/orchestrator.py` | 422 | 300 | -122 | `apply_plan_submission` / `apply_planner_failure` / `_mark_generator` / `_mark_evaluator` share structure (fetch task, assert role/attempt, write status+summary); extract one helper. Inline `_assert_submission_attempt`. |
| `attempt/runtime.py` | 248 | 175 | -73 | Drop the unused `stores` property (no production caller), inline `entry_task_controller_for` (one caller), shorten `LifecycleTarget` Protocol docstrings. |
| `attempt/state.py` | 56 | 56 | 0 | Pure DTOs. Clean. |
| `episode/__init__.py` | 355 | 270 | -85 | After split into `manager.py` + `registry.py`. Convert `_retry_or_close_failed` recursion to a loop, deduplicate startup-failed cleanup against `AttemptOrchestrator._mark_startup_failed`. |
| `episode/state.py` | 104 | 104 | 0 | Pure DTOs. Clean. |
| **Total** | **3,247** | **2,074** | **-1,173** | **≈36% reduction.** |

---

### `mission/handler.py` → ~220 LOC (-201)

**Concrete cuts:**

- **`MissionRepository` (lines 49-109): inline into `MissionHandler`.** Five methods, four of which are 1-line forwarders to `MissionStoreProtocol` (`create`, `get`, `require`, `append_episode_id`). Only `close` (lines 81-109) does work, and even that is "look up mission, build report DTO, call `set_status`." `MissionHandler.close_mission` (lines 395-411) is itself a one-line forwarder to `MissionRepository.close`. Two layers, three methods, no behavior — flatten to one method on `MissionHandler`.
- **`_orchestrator_factory` property + setter (lines 361-367):** `MissionHandler` exposes a property whose getter/setter both reach into `self._factory._orchestrator_factory`. Six lines of indirection for two-finger access. The one external caller, `EpisodeClosureRouter._start_continuation` (line 297), already reaches into `self._factory._orchestrator_factory` directly — proving the property doesn't add encapsulation. Delete the property and the setter; keep `EpisodeFactory._orchestrator_factory` as a plain attribute.
- **`MissionHandler.create_initial_episode_with_manager` (lines 382-385) and `create_continuation_episode_with_manager` (lines 387-390):** Two pure 1-line forwarders to `EpisodeFactory`. Either inline `EpisodeFactory.create_initial` / `create_continuation` into `MissionHandler` directly, or let callers reach `MissionHandler._factory.create_initial` (the closure router already does the equivalent).
- **`EpisodeClosureRouter._latest_attempt_id_for_episode` (lines 319-323):** 5-line helper called once on line 303 (`self._latest_attempt_id_for_episode(next_episode.id) or previous_report.final_attempt_id`). One-shot helper → inline.
- **`nested_mission_depth` (lines 115-155):** ~40-LOC graph walker that belongs nowhere near "mission handler." The single caller is `task_center.agent_routing.predicates`. Move to its own module (e.g. `task_center/mission/ancestry.py`) or to `_core`. The docstring "Phase 7c absorbs `mission/repository.py` and `mission/ancestry.py` into this single module" confirms this was a recent merger; reversing the `ancestry.py` half costs nothing.

**Refactor sketch (target):**

```python
class MissionHandler:
    def __init__(self, *, mission_store, episode_store, attempt_store,
                 manager_registry, config, deliver_closure_report=None,
                 orchestrator_factory=None, task_store=None):
        self._mission_store = mission_store
        self._episode_store = episode_store
        self._attempt_store = attempt_store
        self._manager_registry = manager_registry
        self._config = config
        self._orchestrator_factory = orchestrator_factory
        self._task_store = task_store
        self._deliver_closure_report = deliver_closure_report

    def create_mission(...): ...
    def create_initial_episode(self, *, mission_id) -> tuple[Episode, EpisodeManager]: ...
    def create_continuation_episode(self, *, previous_episode) -> tuple[Episode, EpisodeManager]: ...
    def close_mission(...): ...
    def handle_episode_closed(self, report: EpisodeClosureReport) -> None: ...
```

The single-class `MissionHandler` collapses `Repository + Factory + ClosureRouter` into ~150 LOC. The `_start_continuation` cleanup-on-create-failure branch remains as a private method.

---

### `mission/starter.py` → ~200 LOC (-172)

- **`_default_orchestrator_factory` (lines 67-76):** 9 lines for a 3-line lambda. Move into `__init__` as `self._orchestrator_factory = orchestrator_factory or (lambda attempt, cb: AttemptOrchestrator(attempt=attempt, on_attempt_closed=cb, runtime=self._runtime))`.
- **`_build_handler` (lines 148-167):** Called once from `start()`. The closure `_deliver` (lines 156-157) is a 1-line lambda. Inline both.
- **`_close_unstarted_attempt_after_failed_start` (lines 348-367):** 20 lines for "check attempt isn't closed → close it" — already wrapped in the saga `_do` helper, so the outer try/except is redundant. Collapse to 6 lines.
- **`if initial_attempt is None: raise ...` (lines 133-136):** Dead defensive check. The preceding `try/except` re-raises on failure; the only path past the except is the assignment path. The comment "narrowed by the try block above" + "self-defending under `python -O`" is incorrect — no `assert` is involved, and the check fires on a regular `if`. The block can't trigger under any control-flow. Delete (also fixes the WARNING in §1).
- **`_deliver_synthetic_failure_closure_report` (lines 311-346):** Question whether this last-resort path is justified. It fires only when `_restore_parent` raises, which itself only raises if the lifecycle target is gone or the CAS fails. Document the operational scenarios that require this 35-LOC failure-handler-of-the-failure-handler — if it's defensive theater, delete it; if it's a known recovery, keep it but trim the docstring (lines 319-327 are 9 lines of prose explaining why this exists).

---

### `attempt/launch.py` → ~280 LOC (-188)

This file pays the biggest helper tax. The exhaustion-reporting graph spans **5 functions** for one logical operation:

- `_report_unfinished_running_task` (method, line 176) — entry point: gates on task still being RUNNING.
- `_report_exhaustion` (module-level, line 261) — dispatches by `launch.role`.
- `_require_attempt_orchestrator` (module-level, line 242) — orchestrator lookup; failure path calls `_fail_unowned_attempt`.
- `_fail_unowned_attempt` (classmethod, line 215) — closes attempt + notifies manager.
- `_mark_unowned_task_exhausted` (staticmethod, line 195) — flips task status.

That graph implements one decision tree: *(is there a controller / orchestrator? If yes, dispatch by role; if no, fail attempt + task)*. Inline-flattened, it's ~60 LOC. The current shape (lines 176-321) is 145 LOC and crosses three scopes (instance method, classmethod, module-level functions reaching back into the instance via `launcher._mark_unowned_task_exhausted`). The classmethod / staticmethod / module-function mix is itself a smell — pick one scope.

Other targets in this file:

- **`_ROLE_FAIL_REASONS` (line 323) + `_fail_reason_for_role` (line 330-342):** 20-LOC indirection for a 3-entry dict lookup. The KeyError branch raises `TaskCenterInvariantViolation` only for `ENTRY_EXECUTOR`, but the only caller (`_fail_unowned_attempt`, line 228) is reachable only when `launch.attempt_id is not None` — i.e. never for entry tasks. So the KeyError path is unreachable; the docstring even admits "ENTRY_EXECUTOR is intentionally absent." Replace with `_ROLE_FAIL_REASONS[role]` at the single call site.
- **`_resolve_agent_definition` (staticmethod, lines 92-99):** 8-LOC two-line method called once on line 72. Inline.

**Refactor sketch (target shape):**

```python
async def _report_unfinished_running_task(self, launch, *, summary):
    runtime = self._runtime()
    if runtime is None: return
    task = runtime.task_store.get_task(launch.task_id)
    if task is None or task.get("status") != TaskCenterTaskStatus.RUNNING.value:
        return

    if launch.role == TaskCenterTaskRole.ENTRY_EXECUTOR:
        controller = runtime.entry_task_controller
        if controller is None:
            _fail_unowned_task(runtime, launch, summary=summary)
        else:
            controller.apply_run_exhausted(summary=summary)
        return

    orchestrator = runtime.orchestrator_registry.get(launch.attempt_id or "")
    if orchestrator is None:
        _fail_unowned_attempt(runtime, launch, summary=summary)
        return

    _dispatch_role_failure(orchestrator, launch, summary=summary)
```

Two helpers, no classmethod/staticmethod mix, one dispatch site. ~60 LOC end-to-end.

---

### `attempt/dispatcher.py` → ~220 LOC (-106)

- **`_STAGE_DISPATCH` (line 52) + `getattr` dispatch (line 81):** 2-entry dispatch table consumed via `getattr(self, method)(attempt)`. Replace with `if attempt.stage == AttemptStage.GENERATE: self._dispatch_generating(attempt); elif attempt.stage == AttemptStage.EVALUATE: self._dispatch_evaluating(attempt)`. The table buys nothing for two cases and obscures grep-ability.
- **`_launch_ready_generator` (lines 152-206) + `_launch_evaluator` (lines 208-240):** Near-identical failure-handling structure — wrap launch in try/except, on failure mark task FAILED via `set_task_status_if_current`, emit `task_failed` audit, then either block descendants or close attempt. Extract a single `_handle_launch_failure(launch_or_task_id, attempt_id, role, on_failure_callback)` helper.
- **`_fail_evaluator_spawn` (lines 293-309):** 17 lines. Inline into the one call site at line 290 (`except Exception` block of `_spawn_evaluator`).
- **`_task_agent_name` (lines 311-318):** 7-LOC staticmethod called once. Inline.

---

### `attempt/generator_dag.py` → ~110 LOC (-40)

Three predicates each rebuild the same map and iterate it independently:

```python
def all_generators_quiescent(...): return all(s in TERMINAL_GENERATOR_STATUSES for s in generator_status_map(...).values())
def all_generators_done(...): return all(s == DONE for s in generator_status_map(...).values())
def any_generator_failed_or_blocked(...): return any(s in (FAILED, BLOCKED) for s in generator_status_map(...).values())
```

The call site (`_dispatch_generating`, dispatcher.py:101-130) invokes them serially on the same `task_records` — so each call traverses the same list three times. Collapse to one query:

```python
@dataclass(frozen=True, slots=True)
class GeneratorDagState:
    ready_ids: tuple[str, ...]
    all_quiescent: bool
    all_done: bool
    any_failed_or_blocked: bool

def summarize_generator_dag(task_records: list[TaskRecord]) -> GeneratorDagState: ...
```

Single iteration, single call site, ~30 LOC saved + the dispatcher's branching reads top-to-bottom on one object.

---

### `attempt/orchestrator.py` → ~300 LOC (-122)

- **`_assert_submission_attempt` (lines 417-422):** 6-line helper called from 4 places (`apply_plan_submission`, `apply_planner_failure`, `apply_generator_submission`, `apply_evaluator_submission`). Inline as a one-liner at each site — cheaper than the indirection.
- **`apply_plan_submission` / `apply_planner_failure` / `_mark_generator` / `_mark_evaluator`** all share structure: validate attempt match, fetch task, validate task belongs to attempt + role, write status + summary. Extract `_record_submission(task_id, attempt, expected_role, status, summary)` covering the 4 invocations. Shave ~50 LOC.
- **`apply_planner_failure` (lines 153-181)** ends with `self._close_attempt(AttemptStatus.FAILED, AttemptFailReason.PLANNER_FAILED)`. The body up to that point only marks the planner task FAILED and validates. `_close_attempt` does the actual attempt-close work. The 28-LOC method can be ~15 LOC.
- **`_mark_startup_failed` (lines 369-400):** Duplicates `EpisodeManager._close_attempt_after_startup_failure` (episode/__init__.py:177-191). Both close the attempt with `STARTUP_FAILED`. Pick one owner; orchestrator's path is reachable when `start()` raises before/after `dispatch_ready_work`, while the manager's path is reachable when `_orchestrator_factory(...).start()` raises. The two callers are different but the cleanup logic is identical — extract to a free function in `attempt/state.py` or `_core`.

---

### `attempt/runtime.py` → ~175 LOC (-73)

- **`stores` property (lines 80-100):** 20 LOC for a projection. Grep confirms **zero production callers** — only `test_contexts_protocol_collapse.py` references `TaskCenterStores`. Delete the property and the local-import dance.
- **`entry_task_controller_for` (lines 125-140):** One caller, `lifecycle_target_for` (line 154). Inline:
  ```python
  if attempt_id is None:
      controller = self.entry_task_controller
      return controller if controller is not None and controller.task_id == task_id else None
  ```
- **`AgentLaunch.metadata` field (lines 52-56):** Grep confirms nothing reads `.metadata` and nothing writes it either. Speculative extension point with no current user. Delete the field (5 lines including docstring) — when a knob is needed, add the specific field. The comment block "use for knobs the launcher or runtime can opt into" is exactly the speculative-flexibility shape CLAUDE.md §2 warns against.

---

### `attempt/contexts.py` → ~50 LOC (-74)

Grep across `backend/src/task_center/` shows `AttemptStageCtx`, `EpisodeLifecycleCtx`, `MissionLifecycleCtx` are **referenced only in `attempt/runtime.py:86-88` docstring** (mentioning that they exist), and pinned alive by `test_contexts_protocol_collapse.py`. Zero production classes declare these Protocols as parameter types. They are aspirational documentation.

- `LaunchCtx` IS used (`launch.py:356` — `LaunchBuilder.runtime: LaunchCtx`). Keep.
- `TaskCenterStores` is used as the return type of the `AttemptDeps.stores` property — but that property has no caller. Delete with the property.

After cleanup, this file holds one Protocol (`LaunchCtx`) and a 4-line `__all__`. ~50 LOC.

---

### `episode/__init__.py` → reorganize + ~270 LOC (-85)

(Reorganization in Naming section below — this is the post-split target.)

- **`_retry_or_close_failed` recursion (lines 221-243):** When `create_next_attempt` raises, the method calls itself recursively with the new failed attempt. If retries keep startup-failing, recursion depth equals `episode.attempt_budget`. Rewrite as a loop on the current failed attempt — clearer and not stack-bound by config.
- **`_close_attempt_after_startup_failure` (lines 177-191):** Duplicates `AttemptOrchestrator._mark_startup_failed` (orchestrator.py:369-400). See note above; pick one owner.
- **`_latest_failed_attempt_for` (lines 253-263):** 11 lines, single caller. Inline into `_retry_or_close_failed`.
- **Re-export `__all__` block (lines 338-355):** 18 lines. Acceptable but if `__init__.py` becomes a thin shim post-split, this can shrink.

---

## Naming Issues (criterion 0)

### N1. **HIGH** — `episode/__init__.py` carries 355 LOC of business logic; the rest of the package is empty

`episode/__init__.py` defines `EpisodeManager` (lines 59-314), `EpisodeManagerRegistry` (lines 317-335), and `OrchestratorFactory` (line 54). The sibling `episode/state.py` holds 104 LOC of DTOs. The package therefore has logic split between `__init__.py` (managers, registry, type aliases) and `state.py` (DTOs).

Compare to siblings:
- `attempt/` has 9 files including `state.py`, `orchestrator.py`, `orchestrator_registry.py`, `dispatcher.py`, `launch.py`, `generator_dag.py`, `runtime.py`, `contexts.py` and a 15-LOC `__init__.py` re-export shim.
- `mission/` has `state.py`, `handler.py`, `starter.py`, `close_report_router.py` and an empty `__init__.py`.

The convention across this subsystem is "`__init__.py` is a re-export shim." `episode/` is the lone violator.

**Fix:**

```
episode/
  __init__.py           (re-exports from manager + registry + state — ~25 LOC)
  manager.py            (EpisodeManager + retry logic — ~250 LOC)
  registry.py           (EpisodeManagerRegistry — ~20 LOC)
  state.py              (DTOs, unchanged)
```

This brings episode/ visually in line with attempt/ and resolves "where does `EpisodeManager` live?" without grep.

### N2. **HIGH** — `mission/handler.py` is misnamed: it's four classes glued together

The 421-LOC file holds `MissionRepository`, `EpisodeFactory`, `EpisodeClosureRouter`, `MissionHandler`, and a free function `nested_mission_depth`. Its docstring openly states "Phase 7c absorbs `mission/repository.py` and `mission/ancestry.py` into this single module." Three of the four classes (`MissionRepository`, `EpisodeFactory`, `EpisodeClosureRouter`) are not `Handler`s in any sense — they are repository, factory, and router. The filename promises one class.

**Fix (recommended path):** After the LOC reductions above (collapse `MissionRepository` and `EpisodeFactory` into `MissionHandler`), only two concerns remain — `MissionHandler` and `EpisodeClosureRouter`. Move `EpisodeClosureRouter` to `episode/closure_router.py` (it routes **episode** closure reports, not mission ones — it lives in `mission/` only because `MissionHandler` constructs it). Move `nested_mission_depth` to `mission/ancestry.py` (restoring the file the docstring confesses was merged in). Then `mission/handler.py` becomes a single-class file ~150 LOC, which matches its name.

### N3. **MEDIUM** — `close_report_router.py` and `EpisodeClosureRouter` share the word "router" across two files in the same package

`mission/close_report_router.py:22` defines `MissionClosureReportRouter`. `mission/handler.py:238` defines `EpisodeClosureRouter`. Two "closure routers" in `mission/`, both with the word "router," with different responsibilities (Mission delivery vs. Episode dispatch). The reader can't tell from filenames which is which.

**Fix:** Rename `mission/close_report_router.py` → `mission/closure_report_router.py` (matching the type name `MissionClosureReportRouter`), and move `EpisodeClosureRouter` to `episode/closure_router.py` as in N2. After this, "close report router" lives in `mission/`, "closure router" lives in `episode/`, and the naming reflects the tier.

### N4. **MEDIUM** — `attempt/__init__.py` (15 LOC re-export shim) is inconsistent with `mission/__init__.py` (empty)

`attempt/__init__.py` re-exports `Attempt`, `AttemptFailReason`, `AttemptStage`, `AttemptStatus` from `state.py`. `mission/__init__.py` is empty, meaning every caller imports from `task_center.mission.state` directly (e.g. `starter.py:19-23`). Either both should re-export DTOs or neither should — the convention isn't applied consistently.

**Fix:** Pick one — recommend the empty form. The re-export in `attempt/__init__.py` is consumed by callers writing `from task_center.attempt import Attempt`, which can become `from task_center.attempt.state import Attempt` for one extra word and one less indirection.

### N5. **LOW** — File names within `attempt/` show inconsistent semantics

- `dispatcher.py` — function-name suffix ("the thing that dispatches")
- `orchestrator.py` — function-name suffix ("the thing that orchestrates")
- `launch.py` — verb root ("launching")
- `runtime.py` — noun root ("the runtime")
- `generator_dag.py` — domain-name root ("the DAG helpers")

These aren't wrong individually, but they don't paint a consistent picture. `launch.py` in particular bundles `EphemeralAttemptAgentLauncher` (a class) + `LaunchBuilder` (another class) — the file's docstring even calls it a "Phase 7d merger." A reader looking for the launcher won't expect to also find the launch-builder beside it. Either rename to `agent_launcher.py` or split.

### N6. **LOW** — `runtime.py` bundles `AttemptDeps` (DI container) with `LifecycleTarget` (polymorphic seam) + `GeneratorTaskLifecycle` (impl) + `AgentLaunch` (DTO)

The file's docstring says "Phase 7e merger: bundles the former `attempt/lifecycle.py` (...) into this module." Four unrelated concerns share one file. Same advice as N5: either rename to something neutral (`wiring.py`?) or accept the merger and ensure the docstring is the first thing a reader sees.

---

## Import Depth Violations (criterion 3)

**None.** All 12 files import from `task_center.X.Y` (depth 3) or shallower:

| Pattern | Count | Examples |
|---|---:|---|
| `task_center.mission.state` (depth 3) | 4 | `close_report_router.py:15`, `starter.py:19`, `handler.py:35`, `orchestrator.py:10` |
| `task_center.attempt.state` (depth 3) | 5 | `__init__.py:3`, `dispatcher.py:17`, `launch.py:20`, `orchestrator.py:13`, `runtime.py:17` |
| `task_center.attempt.runtime` (depth 3) | 4 | `close_report_router.py:13`, `starter.py:28`, `dispatcher.py:24`, `orchestrator.py:20` |
| `task_center._core.types` (depth 3) | many | All files touching invariants |
| `task_center._core.infra` (depth 3) | 4 | `dispatcher.py:15`, `handler.py:29`, `orchestrator.py:40`, `episode/__init__.py:16` |
| `task_center._core.persistence` (depth 3) | 4 | `contexts.py:23`, `handler.py:36`, `runtime.py:21`, `episode/__init__.py:23` |
| `task_center.context_engine.scope` (depth 3) | 2 | `dispatcher.py:23` (dead, see B1), `orchestrator.py:19` (dead, see B1) |
| `task_center.task_state` (depth 2) | 6 | various |
| `task_center.episode` (depth 2) | 3 | `runtime.py:19`, `handler.py:14`, `starter.py:26` |

External (non-`task_center`) imports — all depth ≤ 2:
- `agents.get_definition`, `audit.base.AuditSink`/`NoopAuditSink`, `message.stream_events.StreamEvent`, `runtime.app_factory.RuntimeConfig`, `tools.ExecutionMetadata`, `engine.api.run_ephemeral_agent`.

No `task_center._core.X.Y` or `task_center.X.Y.Z` chains exist in these 12 files.

---

## Implementation Quality Issues (criterion 1)

### B1. **BLOCKER (dead code, easy to spot)** — `ContextScope` is imported but unused in two files

- `attempt/dispatcher.py:23` — `from task_center.context_engine.scope import ContextScope`
- `attempt/orchestrator.py:19` — `from task_center.context_engine.scope import ContextScope`

`grep -n "ContextScope"` against both files returns only the import lines. The actual `ContextScope` usage lives in `attempt/launch.py:21` (via `LaunchBuilder.for_planner/_generator/_evaluator/_entry`). Two dead imports.

**Fix:** Delete both import lines.

### B2. **WARNING** — `MissionStarter.start` (mission/starter.py:131-136) has an unreachable defensive check

```python
if initial_attempt is None:
    raise TaskCenterInvariantViolation(
        "MissionStarter.start completed without assigning initial_attempt."
    )
```

The comment claims this defends against `python -O`. But no `assert` precedes it — this is a regular `if`, which Python never strips under `-O`. The preceding `try` block either assigns `initial_attempt` and falls through to here, or raises and exits the function via `raise` in the `except` clause. There is no control-flow path on which `initial_attempt` is `None` at line 133.

**Fix:** Delete lines 131-136. The variable is provably non-None at the return on line 137.

### B3. **WARNING** — `MissionHandler._orchestrator_factory` property + setter (mission/handler.py:361-367) reach into a sibling's private attribute

```python
@property
def _orchestrator_factory(self) -> OrchestratorFactory | None:
    return self._factory._orchestrator_factory

@_orchestrator_factory.setter
def _orchestrator_factory(self, value: OrchestratorFactory | None) -> None:
    self._factory._orchestrator_factory = value
```

Two layers of encapsulation violation: a property whose getter reads, and setter writes, `self._factory._orchestrator_factory`. The setter is used by exactly one test (`test_phase04_mission_request_start.py:170: handler._orchestrator_factory = _failing_factory`).

This is allowed by Python but signals that the `MissionRepository / EpisodeFactory / EpisodeClosureRouter / MissionHandler` factoring is the wrong shape for the existing call sites — they want to reach across the seams. Either:
- Collapse the layers (preferred, see LOC reduction §`mission/handler.py`), or
- Make `_orchestrator_factory` a public field on `EpisodeFactory` and let the test set it directly.

### B4. **WARNING** — `_retry_or_close_failed` (episode/__init__.py:221-243) recurses where a loop suffices

When `create_next_attempt` raises during retry, the method recurses on the new failed attempt:

```python
self._retry_or_close_failed(retry_attempt)
```

Stack depth is bounded by `episode.attempt_budget` (config), so this won't blow the stack at default settings — but a loop is simpler to reason about and not config-bound:

```python
def _retry_or_close_failed(self, attempt):
    while True:
        episode = self._current_episode_snapshot()
        if not episode.has_budget_remaining:
            self._close_episode_failed(attempt)
            return
        try:
            self.create_next_attempt(previous_attempt_id=attempt.id)
            return
        except Exception:
            retry = self._latest_failed_attempt_for(previous_id=attempt.id)
            if retry is None:
                raise
            logger.warning(...)
            attempt = retry
            continue
```

### B5. **WARNING** — `AgentLaunch.metadata` is a speculative extension point with zero readers and zero writers

`attempt/runtime.py:52-56`:

```python
# Per-launch extension bag. Use for knobs the launcher or runtime can
# opt into (priority, latency budget, retry policy) without forcing a
# new field + four call-site edits per knob. ...
metadata: dict[str, Any] = field(default_factory=dict)
```

`grep -rn "launch.metadata\|\.metadata =" backend/src/task_center/` returns no production reader or writer. Adding speculative "extension bags" is exactly the pattern CLAUDE.md §2 prohibits ("No 'flexibility' or 'configurability' that wasn't requested"). Delete the field and the docstring comment.

### B6. **WARNING** — `mission/close_report_router.py:36` has redundant null-funnel

```python
attempt_id = str(task.get("task_center_attempt_id") or "") or None
```

If `task.get(...)` returns `None`, this evaluates: `str(None or "")` → `str("")` → `""` → `None`. If it returns a non-empty string, this evaluates: `str(str_val or "")` → `str(str_val)` → `str_val` → `str_val`. If it returns an empty string (unlikely but possible), this also yields `None`.

Equivalent: `attempt_id = task.get("task_center_attempt_id") or None` (assuming the value is already a `str | None`, which the task-store contract should enforce). Or be explicit: `raw = task.get("task_center_attempt_id"); attempt_id = raw if isinstance(raw, str) and raw else None`.

This pattern repeats at `mission/starter.py:370-372` (`_parent_attempt_id`):
```python
def _parent_attempt_id(task: dict[str, Any]) -> str | None:
    raw = str(task.get("task_center_attempt_id") or "")
    return raw if raw else None
```
Same simplification applies.

### B7. **WARNING** — Three converging recovery paths in `launch.py:_run_launch` mask their differences

`attempt/launch.py:124-168` handles three failure modes, all funneling through `_report_unfinished_running_task`:

1. Runner raises (lines 124-148, `except Exception as exc`)
2. Runner returns `None` (lines 155-160)
3. Runner returns an object whose `.status == "failed"` or any other state (lines 162-168)

For mode 1, the summary includes the exception (`f"Agent run crashed: {exc}"`). For mode 2, it's a fixed string. For mode 3, the summary depends on `getattr(result, "error", None)`.

This is fine — but the `# pragma: no cover - defensive runner boundary` on the `except Exception` (line 135) suggests modes 1 and 2/3 are operationally distinct and the project has not bothered to test the exception path. If runner exceptions are a real production case, this code is one-shot-untested. If they aren't, the entire `try/except Exception` can be removed and `await runner(...)` allowed to bubble.

**Recommend:** Decide whether runner-exception is a supported case. If yes, write a test. If no, delete the `try/except`.

### B8. **WARNING** — Duplicate "startup-failed cleanup" logic between orchestrator and manager

- `attempt/orchestrator.py:369-400` — `_mark_startup_failed`: marks planner task FAILED via CAS, then closes attempt as STARTUP_FAILED.
- `episode/__init__.py:177-191` — `_close_attempt_after_startup_failure`: closes attempt as STARTUP_FAILED.

Two owners for the same recovery. The orchestrator path triggers when `AttemptOrchestrator.start()` raises (planner upsert / launch). The manager path triggers when `orchestrator_factory(...).start()` raises (caught in `_start_orchestrator_if_configured`). The manager's path runs AFTER the orchestrator's `_mark_startup_failed`, so the second call is a no-op (the attempt is already closed and the `if latest is None or latest.is_closed: return` guard early-exits). But the duplication invites future drift — pick one owner.

### B9. **WARNING** — `AttemptDispatcher._STAGE_DISPATCH` table is a 2-entry dispatcher consumed via `getattr`

`attempt/dispatcher.py:52-55`:

```python
_STAGE_DISPATCH: dict[AttemptStage, str] = {
    AttemptStage.GENERATE: "_dispatch_generating",
    AttemptStage.EVALUATE: "_dispatch_evaluating",
}
```

Then line 81: `getattr(self, method)(attempt)`.

Two entries, dynamic-attribute lookup, not greppable from the call site. For two cases, a plain `if/elif` is cheaper and more grep-friendly. Tables justify themselves at ≥4 entries, not 2.

### B10. **Info** — `_fail_reason_for_role` (`launch.py:330-342`) raises on an unreachable input

The function maps role → fail-reason for three roles (`PLANNER`, `GENERATOR`, `EVALUATOR`). `ENTRY_EXECUTOR` is "intentionally absent" because its only caller (`_fail_unowned_attempt`, line 228) short-circuits when `launch.attempt_id is None` — which is true exactly for entry tasks.

So the `except KeyError` branch is unreachable today. The function is defensive about a case the caller already prevents. Two options:

- Inline the dict lookup at the call site, drop the function (preferred, see LOC reduction).
- Keep the function but stop raising — let `KeyError` propagate, which signals a contract violation to the caller without the wrapper class.

### B11. **Info** — `EpisodeClosureRouter._start_continuation` mutes exceptions and continues compensating

`mission/handler.py:299-317`:

```python
try:
    next_manager.create_initial_attempt()
except Exception:
    failed_attempt_id = (
        self._latest_attempt_id_for_episode(next_episode.id)
        or previous_report.final_attempt_id
    )
    self._episode_store.set_status(next_episode.id, status=EpisodeStatus.CANCELLED, ...)
    self._manager_registry.deregister(next_episode.id)
    self._close_mission(...)
```

Bare `except Exception:` with no logging on the swallowed exception. If `create_initial_attempt` raises for an unexpected reason (e.g. attempt_store contract failure), we cancel the episode + fail the mission without recording why. This is a defensible compensation pattern, but missing a `logger.exception(...)` line at the top of the `except` body.

**Fix:** Add `logger.exception("EpisodeClosureRouter: continuation attempt creation failed", extra={"episode_id": next_episode.id})` as the first line of the `except` block.

### B12. **Info** — `generator_dag.ordered_generator_tasks` builds a topological sort but never returns the cycle members

`attempt/generator_dag.py:47-49`:

```python
if len(ordered) != len(tasks):
    raise TaskCenterInvariantViolation("Generator plan contains a dependency cycle")
```

Useful as-is, but a developer debugging a cycle has to manually compare `tasks` against `ordered`. Trivial improvement: `tuple(t.local_id for t in tasks if t.local_id not in {o.local_id for o in ordered})` in the message. Same pattern at line 22 for duplicate IDs.

---

## Per-File Notes

### `mission/__init__.py` (0 LOC)
Empty. Fine. See N4 for consistency consideration against `attempt/__init__.py`.

### `mission/close_report_router.py` (72 → 60 LOC)
Tight single-class file. Issues: B6 (null-funnel), and one structural critique — the file is co-named with `EpisodeClosureRouter` inside `mission/handler.py` (N3). Otherwise clean.

### `mission/handler.py` (421 → ~220 LOC)
The biggest LOC win in this review. Multi-class grab-bag, three layers of indirection (`MissionRepository → EpisodeFactory → EpisodeClosureRouter` all owned by `MissionHandler`), property setter reaching into private attribute (B3). See LOC reduction §`mission/handler.py`.

### `mission/starter.py` (372 → ~200 LOC)
Compensation saga is real and required; the wrapper helpers around it are not. Dead defensive check at lines 131-136 (B2). 35-LOC last-resort recovery (`_deliver_synthetic_failure_closure_report`) needs operational justification or deletion.

### `mission/state.py` (67 LOC)
Pure DTOs. No reduction opportunity, file is at target.

### `attempt/__init__.py` (15 LOC)
Thin re-export. Inconsistent with `mission/__init__.py` (N4). Acceptable.

### `attempt/contexts.py` (124 → ~50 LOC)
Three of four Protocols (`AttemptStageCtx`, `EpisodeLifecycleCtx`, `MissionLifecycleCtx`) have no production consumer; only `LaunchCtx` does. `TaskCenterStores` is used as the return type of an unused property. Mostly deletable.

### `attempt/dispatcher.py` (326 → ~220 LOC)
Solid logic. Issues: 2-entry `_STAGE_DISPATCH` (B9), dead `ContextScope` import (B1), generator/evaluator failure-handling near-duplication. See LOC reduction §`attempt/dispatcher.py`.

### `attempt/generator_dag.py` (150 → ~110 LOC)
Pure functions, easy to read. Three boolean predicates iterate the same map independently — consolidatable into one `summarize` call (see LOC reduction). Cycle-error message can name the offending tasks (B12).

### `attempt/launch.py` (468 → ~280 LOC)
The biggest helper-tax payer in the review. Five-function exhaustion-reporter graph with `classmethod` / `staticmethod` / module-level scope mixing. `_fail_reason_for_role` indirection has unreachable error path (B10). Three converging failure modes in `_run_launch` mask their differences (B7).

### `attempt/orchestrator_registry.py` (47 LOC)
Tight, single-purpose. No reduction opportunity, file is at target.

### `attempt/orchestrator.py` (422 → ~300 LOC)
Clean state machine. Issues: dead `ContextScope` import (B1), four `apply_*` methods share boilerplate (LOC reduction), `_assert_submission_attempt` 4-callsite helper is shorter when inlined, duplicate startup-failed cleanup against `EpisodeManager` (B8).

### `attempt/runtime.py` (248 → ~175 LOC)
Bundles DI + lifecycle seam + DTO. Unused `stores` property (-20 LOC), 1-caller `entry_task_controller_for` (-15 LOC), speculative `AgentLaunch.metadata` extension bag (B5).

### `attempt/state.py` (56 LOC)
Pure DTOs. No reduction opportunity, file is at target.

### `episode/__init__.py` (355 → ~270 LOC after split)
**Biggest naming issue in the subsystem** (N1). Move `EpisodeManager` to `episode/manager.py`, `EpisodeManagerRegistry` to `episode/registry.py`, leave a thin re-export. Code issues: B4 (recursion → loop), B8 (duplicate startup cleanup).

### `episode/state.py` (104 LOC)
Pure DTOs with closure-outcome variants. No reduction opportunity, file is at target.

---

## Recommended Next Steps

In order of "LOC reduction per unit of effort":

1. **Delete the easy stuff first** (~100 LOC, ~10 minutes):
   - `ContextScope` dead imports (B1).
   - `MissionStarter.start` unreachable check (B2).
   - `AgentLaunch.metadata` field (B5).
   - Two `_*_attempt_id` null-funnels (B6).
   - 18-line `__all__` block consolidation.

2. **Collapse `mission/handler.py`** (~200 LOC saved, ~1 hour): inline `MissionRepository` and `EpisodeFactory` into `MissionHandler`; delete the `_orchestrator_factory` property/setter (B3); move `nested_mission_depth` to `mission/ancestry.py`.

3. **Refactor `attempt/launch.py` exhaustion graph** (~150 LOC saved, ~1-2 hours): collapse the 5-function `_report_*` / `_fail_unowned_*` / `_mark_unowned_*` cluster into 2 functions; delete `_fail_reason_for_role` and `_ROLE_FAIL_REASONS` lookup (B10).

4. **Split `episode/__init__.py`** (no LOC change, structural fix, ~15 minutes): `manager.py` + `registry.py` + thin `__init__.py`. Address the biggest naming issue in the subsystem (N1).

5. **Delete unused Protocols** (~70 LOC saved, ~10 minutes): drop `AttemptStageCtx`, `EpisodeLifecycleCtx`, `MissionLifecycleCtx`, `TaskCenterStores`, and the corresponding test (`test_contexts_protocol_collapse.py`). They survive only because the test pins them.

After steps 1-5: ~3,247 → ~2,500 LOC (≈23% reduction) with no behavior change. The remaining reductions in `attempt/orchestrator.py`, `attempt/dispatcher.py`, `attempt/generator_dag.py`, and `mission/starter.py` are higher-effort and can wait — but each step is independently safe.

---

_Reviewed: 2026-05-15_
_Focus: naming / impl quality / simplicity / import depth_
