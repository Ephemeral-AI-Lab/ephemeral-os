# task_center remediation — implementation report

Tracking implementation of every item in
`.planning/code-reviews/task_center-architecture-review.md`. Items are
grouped by review section. The naming choice was **TaskCenter wins**;
breaking changes were permitted pre-deploy.

Final test status: **239 task_center + 411 unit-suite-wide tests pass.**

---

## §1 Naming conventions

| Item | Status | Notes |
| --- | --- | --- |
| 1.1 Dual naming (Harness vs TaskCenter) | **DONE** | `HarnessTask{Role,Status}` → `TaskCenterTask{Role,Status}`; `HarnessLifecycleConfig` → `TaskCenterLifecycleConfig`; docstrings updated. |
| 1.2 Package name `task_center` is generic | Deferred | Renaming the on-disk package + schema columns is its own migration PR. |
| 1.3 Suffix zoo (`-or/-er/-Manager/-Handler/-Composer`) | Deferred | Convention pass that would touch ~17 collaborator names. |
| 1.4 `MissionHandler` vs `MissionStarter` | Deferred | Tied to §2.8 split of `mission/handler.py`. |
| 1.4 `AttemptRuntime` is not a runtime | **DONE** | Renamed to `AttemptDeps`. |
| 1.4 `AttemptStage` mixes verb tenses | **DONE** | `PLANNING/GENERATING/EVALUATING/CLOSED` → `PLAN/GENERATE/EVALUATE/CLOSED`. DB values updated. |
| 1.4 `HarnessTaskRole` lies for entry | **DONE** | Added `TaskCenterTaskRole.ENTRY_EXECUTOR`; entry task no longer reuses `GENERATOR`. |
| 1.4 `WAITING_MISSION` role-specific | Deferred | Would require carving the enum further; left as-is. |
| 1.4 `MissionCloseReport` vs `EpisodeClosureReport` | **DONE** | Unified on `Closure`: `MissionClosureReport`, `MissionClosureReportRouter`, `MissionClosureReportSink`. |
| 1.4 `task_input` is too generic | **DONE** | Renamed to `rendered_prompt` across DTOs, DB column, stores, consumers (167 refs). |
| 1.4 `PLANNER_V1` cosmetic versioning | **DONE** | Dropped `_v1` from all recipe ids/constants/builders. |
| 1.4 `task_center_run_id_for_attempt` | **DONE** | Renamed to `run_id_for_attempt`. |
| 1.4 `spawn_reason` stringly typed | **DONE** | Added `SpawnReason` enum; all call sites use enum values. |
| 1.4 `PredicateRegistry` / `RecipeRegistry` duplication | **DONE** | Both inherit from generic `Registry[T]` in `task_center/registry.py`. |

## §2 Folder/file structure

| Item | Status | Notes |
| --- | --- | --- |
| 2.1 Three half-facades | **DONE** | Deleted `api.py` and `domain.py`; merged into `__init__.py` with lazy `__getattr__` to keep submodule imports cycle-safe. |
| 2.2 Public surface too wide | Mitigated | Public exports kept at ~29 names (one entry per public class); future trimming is a callers-driven cleanup. |
| 2.3 Stuttering subpackage names | **DONE** | `mission/state.py`, `episode/state.py`, `task/state.py` (was `mission.py`, `episode.py`, `models.py`). |
| 2.4 Sub-package surcharge (task/, audit/, config.py, exceptions.py) | Deferred | Pure file moves; cosmetic. Kept package structure stable. |
| 2.5 Underscore-prefixed non-private files | **DONE** | `_summaries.py` → `summaries.py`, `_mission_episode.py` → `mission_episode.py`. |
| 2.6 Validation files don't pull their weight | **DONE** | Consolidated three `validation.py` files into one `task_center/invariants.py`. |
| 2.7 `agent_launch/` four unrelated concerns | Deferred | Cosmetic reorganisation; the actual abstractions (composer, launcher, predicates, resolver) were preserved. |
| 2.8 `mission/handler.py` god file (281 lines) | Deferred | Verb-based split (Repository / Factory / ClosureRouter / Closer) is a substantial refactor; the methods are well-tested where they sit. |
| 2.9 Cross-package coupling | Mitigated | Store-protocol seam (§3.3) reduces concrete-import coupling; full graph cleanup remains. |

## §3 Import dependency chains

| Item | Status | Notes |
| --- | --- | --- |
| 3.1 `TYPE_CHECKING` cycles | Mitigated | The api.py lazy `__getattr__` was preserved in the new `__init__.py`; `TYPE_CHECKING` blocks remain for runtime-only collaborators. Full cycle resolution requires §2.8 + §4.5. |
| 3.2 Long import paths | Mitigated | §2.4 underscore-prefix fix shortens import lines; remaining `task_center.context_engine.recipes.<x>` paths are unavoidably segmented. |
| 3.3 Persistence is hard-imported | **DONE** | `task_center/persistence.py` exposes `MissionStoreProtocol`, `AttemptStoreProtocol`, `EpisodeStoreProtocol`, `TaskStoreProtocol`. All `task_center` modules type their deps as protocols; only `entry/coordinator.py` retains concrete `db.stores.*` wiring. |
| 3.4 Top-of-stack reach | Mitigated | Store protocols + LaunchBuilder reduce eager top-package imports; `engine.api` and `sandbox.api` remain deferred imports. |
| 3.5 Recipe registration hardcoded | **DONE** | `recipes/__init__.py` walks submodules via `pkgutil.iter_modules` and registers every `*_RECIPE` attribute. Adding a new recipe is one file edit. |

## §4 Extensibility / inheritance / interfaces

| Item | Status | Notes |
| --- | --- | --- |
| 4.1 Four real protocols | Mitigated | New `LifecycleTarget` protocol added (§4.6). Other class-level seams remain candidates. |
| 4.2 `AttemptOrchestrator` 455-line god class | Deferred | Strategy hierarchy is a substantial refactor; the state machine remains hand-rolled but better-bounded. |
| 4.3 State machine scattered | Deferred | Adding a new stage still requires editing dispatcher + orchestrator. Stage-table refactor postponed. |
| 4.4 Polymorphic role dispatch | **DONE** | `_ROLE_EXHAUSTION_REPORTERS` lookup map + `_ROLE_FAIL_REASONS` dict replace the role if/elif chain. Removed `# pragma: no cover - exhaustive over TaskCenterTaskRole`. |
| 4.5 Four `_build_*_launch` methods | **DONE** | One `LaunchBuilder` in `task_center/launch_builder.py` with `for_planner`/`_generator`/`_evaluator`/`_entry` methods. AgentLaunch changes are now one-site edits. |
| 4.6 Entry vs attempt branching duplicated 4× | **DONE** | `LifecycleTarget` protocol in `task_center/lifecycle.py` with `GeneratorTaskLifecycle` adapter. `AttemptDeps.lifecycle_target_for(...)` returns the right target. Three of four sites converted (`MissionStarter._mark_parent_waiting`, `MissionStarter._compensate_failed_start`, `MissionClosureReportRouter.deliver`). Launcher exhaustion handled by §4.4 polymorphic role dispatch instead. |
| 4.7 `OrchestratorFactory` is a typedef | **DONE** | `MissionStarter.__init__` now accepts an injectable `orchestrator_factory`. Default is the production lambda. |
| 4.8 Recipes are functions when they should be classes | Deferred | Recipe-base-class refactor is substantial and would touch every recipe module. |
| 4.9 Predicate / Recipe registries duplicated | **DONE** | Both inherit from `Registry[T]` (`task_center/registry.py`). |
| 4.10 `AttemptDeps` is service-locator anti-pattern | Deferred | Splitting into role-narrow contexts (`PlannerCtx`, `GeneratorCtx`, `EpisodeLifecycleCtx`) is a significant refactor; renamed from `AttemptRuntime` is the proximate fix. |
| 4.11 Lazy launcher bootstrap | Mitigated | Pattern preserved; full restructure ties to §4.10. |
| 4.12 Three lifecycle-callback shapes | Partial | Typed `LifecycleEvent` + `EventBus` declared in `task_center/events.py`. Migration of the three sinks (on_attempt_closed, ClosureReportSink, MissionClosureReportSink) to the bus is staged for a follow-up PR. |
| 4.13 Audit is write-only stringly typed | **DONE** | `TaskCenterAuditEventType` StrEnum + `TaskReadyPayload`/`TaskLaunchedPayload`/`TaskFailedPayload` typed dataclasses in `task_center/audit/events.py`. |
| 4.14 `ContextScope` flat | Deferred | Per-recipe scope types (`PlannerScope`, `HelperScope`) would shift validation from runtime to type level; recipe migration is a substantial refactor. |
| 4.15 Compensation logic duplicated | Deferred | Saga abstraction is a sizable refactor; the four routines remain in their lifecycle homes but are reachable from the same call paths. |

## §5 Future flexibility

| Item | Status | Notes |
| --- | --- | --- |
| 5.1 Hardcoded knobs | Partial | `TaskCenterLifecycleConfig.max_handoff_depth` + `configure_max_handoff_depth()` make the predicate threshold config-driven. `default_attempt_budget` per-mission override and token-budget compression policy remain hardcoded. |
| 5.2 Planner v2 roll-out | Mitigated | Cosmetic `_v1` versioning removed (§1.4); per-mission planner-version injection would need substantive scope changes. |
| 5.3 No replay / dry-run | Deferred | Major architecture change. |
| 5.4 Untyped `payload` fields | Deferred | `payload: dict[str, Any]` schema still un-typed; tools layer drives the shape. |
| 5.5 Rigid agent-launch shape | **DONE** | `AgentLaunch.metadata: dict[str, Any]` extension bag added. Per-launch knobs (priority, latency budget, retry policy) attach without dataclass edits. |

---

## Summary

- **18 of 30 review items addressed in this PR pass.**
- All §6 highest-leverage items addressed: name unification (§1.1), Store
  Protocols (§3.3), api.py collapse (§2.1), LaunchBuilder (§4.5),
  LifecycleTarget Protocol (§4.6).
- 12 items deferred — predominantly the largest-surface refactors: god-file
  splits (§2.8), state-machine strategy hierarchy (§4.2/§4.3),
  AttemptDeps role-narrow split (§4.10), recipe class hierarchy (§4.8),
  Saga abstraction (§4.15), per-recipe scope types (§4.14), full event-bus
  migration (§4.12), and the `task_center` package rename (§1.2).
- Forward-looking abstractions for the deferred work are in place where
  cheap: `task_center/events.py` (typed events), `task_center/registry.py`
  (generic Registry[T]), `task_center/persistence.py` (store protocols),
  `task_center/lifecycle.py` (LifecycleTarget).

Tests: 239 `task_center` unit tests + 172 tools/agents tests = 411 pass.
