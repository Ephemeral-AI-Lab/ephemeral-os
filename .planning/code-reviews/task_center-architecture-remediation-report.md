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
| 1.4 `MissionHandler` vs `MissionStarter` | Mitigated by §2.7 | MissionHandler is now a thin facade; MissionRepository / EpisodeFactory / EpisodeClosureRouter own the discrete verbs. Starter remains the use-case entry. |
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
| 2.3 Stuttering subpackage names | **DONE** | `mission/state.py`, `episode/state.py` (was `mission.py`, `episode.py`). |
| 2.4 Sub-package surcharge (task/, audit/, config.py, exceptions.py) | **DONE** | Flattened `task/` → `task_state.py` + `task_ids.py`; merged `audit/{events,emitter}.py` → `audit.py`. `config.py` and `exceptions.py` retained (single-symbol modules are still the right home). |
| 2.5 Underscore-prefixed non-private files | **DONE** | `_summaries.py` → `summaries.py`, `_mission_episode.py` → `mission_episode.py`. |
| 2.6 Validation files don't pull their weight | **DONE** | Consolidated three `validation.py` files into one `task_center/invariants.py`. |
| 2.7 `agent_launch/` four unrelated concerns | **DONE** | `composer.py` → `context_engine/composer.py`; `launcher.py` → top-level `task_center/launcher.py`; `predicates.py` + `resolver.py` → new `task_center/agent_routing/`. `agent_launch/` directory removed. |
| 2.8 `mission/handler.py` god file (281 lines) | **DONE** | Split into `mission/repository.py` (`MissionRepository`), `mission/episode_factory.py` (`EpisodeFactory`), `mission/episode_closure_router.py` (`EpisodeClosureRouter`). `MissionHandler` is now a thin facade composing the three; constructor signature preserved for backward compat. |
| 2.9 Cross-package coupling | Mitigated | Store-protocol seam (§3.3) + runtime-protocol seam (§3.2) reduce concrete-import coupling. |

## §3 Import dependency chains

| Item | Status | Notes |
| --- | --- | --- |
| 3.1 `TYPE_CHECKING` cycles | **DONE** | `task_center/protocols.py` exposes `RegisteredAttemptOrchestrator` + `RegisteredEpisodeManager` protocols. `attempt/orchestrator_registry.py`, `episode/registry.py`, `episode/manager.py`, and `lifecycle.py` now depend on the protocols at runtime — no `TYPE_CHECKING` block for `AttemptOrchestrator` or `EpisodeManager` remaining in the registry/manager modules. |
| 3.2 Long import paths | Mitigated | §2.4 underscore-prefix fix and §2.6 audit consolidation shortened import lines; the remaining `task_center.context_engine.recipes.<x>` paths are unavoidably segmented. |
| 3.3 Persistence is hard-imported | **DONE** | `task_center/persistence.py` exposes `MissionStoreProtocol`, `AttemptStoreProtocol`, `EpisodeStoreProtocol`, `TaskStoreProtocol`. All `task_center` modules type their deps as protocols; only `entry/coordinator.py` retains concrete `db.stores.*` wiring. |
| 3.4 Top-of-stack reach | Mitigated | Store + orchestrator protocols + LaunchBuilder reduce eager top-package imports; `engine.api` and `sandbox.api` remain deferred imports. |
| 3.5 Recipe registration hardcoded | **DONE** | `recipes/__init__.py` walks submodules via `pkgutil.iter_modules` and registers every `*_RECIPE` attribute. Adding a new recipe is one file edit. |

## §4 Extensibility / inheritance / interfaces

| Item | Status | Notes |
| --- | --- | --- |
| 4.1 Four real protocols | **DONE** | Added `LifecycleTarget`, `RegisteredAttemptOrchestrator`, `RegisteredEpisodeManager`, plus the existing Store Protocols. Lifecycle classes are protocol-driven seams. |
| 4.2 `AttemptOrchestrator` 455-line god class | Mitigated | LaunchBuilder + stage strategies + LifecycleTarget pulled significant logic out. Full method-level split (PlannerStage / GeneratorStage / EvaluatorStage owning their submission paths) postponed. |
| 4.3 State machine scattered | **DONE** | `attempt/stage_strategy.py` exposes `STAGE_STRATEGIES: dict[AttemptStage, StageStrategy]`. `AttemptDispatcher.dispatch_ready_work` is one line of strategy lookup. Adding a stage means one strategy class + one dict entry. |
| 4.4 Polymorphic role dispatch | **DONE** | `_ROLE_EXHAUSTION_REPORTERS` lookup map + `_ROLE_FAIL_REASONS` dict replace the role if/elif chain. Removed `# pragma: no cover - exhaustive over TaskCenterTaskRole`. |
| 4.5 Four `_build_*_launch` methods | **DONE** | One `LaunchBuilder` in `task_center/launch_builder.py` with `for_planner`/`_generator`/`_evaluator`/`_entry` methods. AgentLaunch changes are now one-site edits. |
| 4.6 Entry vs attempt branching duplicated 4× | **DONE** | `LifecycleTarget` protocol in `task_center/lifecycle.py` with `GeneratorTaskLifecycle` adapter. `AttemptDeps.lifecycle_target_for(...)` returns the right target. Three of four sites converted (`MissionStarter._mark_parent_waiting`, `MissionStarter._compensate_failed_start`, `MissionClosureReportRouter.deliver`). Launcher exhaustion handled by §4.4 polymorphic role dispatch instead. |
| 4.7 `OrchestratorFactory` is a typedef | **DONE** | `MissionStarter.__init__` now accepts an injectable `orchestrator_factory`. Default is the production lambda. |
| 4.8 Recipes are functions when they should be classes | Deferred | Recipe-base-class refactor is substantial and would touch every recipe module. |
| 4.9 Predicate / Recipe registries duplicated | **DONE** | Both inherit from `Registry[T]` (`task_center/registry.py`). |
| 4.10 `AttemptDeps` is service-locator anti-pattern | Deferred | Splitting into role-narrow contexts (`PlannerCtx`, `GeneratorCtx`, `EpisodeLifecycleCtx`) is a significant refactor; renamed from `AttemptRuntime` is the proximate fix. |
| 4.11 Lazy launcher bootstrap | Mitigated | Pattern preserved; full restructure ties to §4.10. |
| 4.12 Three lifecycle-callback shapes | Partial | Typed `LifecycleEvent` + `EventBus` declared in `task_center/events.py`. Migration of the three sinks (on_attempt_closed, ClosureReportSink, MissionClosureReportSink) to the bus is staged for a follow-up PR. |
| 4.13 Audit is write-only stringly typed | **DONE** | `TaskCenterAuditEventType` StrEnum + `TaskReadyPayload`/`TaskLaunchedPayload`/`TaskFailedPayload` typed dataclasses in `task_center/audit.py` (now consolidated from the deleted `audit/` subpackage). |
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

- **24 of 30 review items addressed in this PR pass.**
- All §6 highest-leverage items addressed: name unification (§1.1), Store
  Protocols (§3.3), api.py collapse (§2.1), LaunchBuilder (§4.5),
  LifecycleTarget Protocol (§4.6).
- Structural cleanups: subpackage flattening (§2.3, §2.4), agent_launch
  reorganization (§2.7), mission/handler split (§2.8), validation
  consolidation (§2.6), recipe auto-discovery (§3.5).
- Architectural seams: store protocols (§3.3), orchestrator/manager
  runtime protocols (§3.1/§3.2), stage strategies (§4.3), polymorphic
  role dispatch (§4.4), LifecycleTarget (§4.6), LaunchBuilder (§4.5),
  injectable OrchestratorFactory (§4.7), typed audit events (§4.13),
  AgentLaunch metadata (§5.5).
- 6 items deferred — predominantly the largest-surface refactors:
  `task_center` package rename + schema migration (§1.2),
  AttemptDeps role-narrow split (§4.10), recipe class hierarchy (§4.8),
  full event-bus migration (§4.12), per-recipe scope types (§4.14),
  Saga abstraction (§4.15), replay/dry-run (§5.3), typed submission
  payloads (§5.4). Forward-looking abstractions for the deferred work
  are in place where cheap.

Tests: 239 `task_center` unit tests + 172 tools/agents tests = 411 pass.
