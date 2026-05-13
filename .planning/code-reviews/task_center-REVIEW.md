---
phase: task_center (ad-hoc directory review)
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 55
files_reviewed_list:
  - backend/src/task_center/__init__.py
  - backend/src/task_center/api.py
  - backend/src/task_center/config.py
  - backend/src/task_center/domain.py
  - backend/src/task_center/exceptions.py
  - backend/src/task_center/agent_launch/__init__.py
  - backend/src/task_center/agent_launch/composer.py
  - backend/src/task_center/agent_launch/launcher.py
  - backend/src/task_center/agent_launch/predicates.py
  - backend/src/task_center/agent_launch/resolver.py
  - backend/src/task_center/attempt/__init__.py
  - backend/src/task_center/attempt/dispatcher.py
  - backend/src/task_center/attempt/factory.py
  - backend/src/task_center/attempt/generator_dag.py
  - backend/src/task_center/attempt/orchestrator.py
  - backend/src/task_center/attempt/orchestrator_registry.py
  - backend/src/task_center/attempt/runtime.py
  - backend/src/task_center/attempt/state.py
  - backend/src/task_center/attempt/validation.py
  - backend/src/task_center/context_engine/__init__.py
  - backend/src/task_center/context_engine/engine.py
  - backend/src/task_center/context_engine/errors.py
  - backend/src/task_center/context_engine/packet.py
  - backend/src/task_center/context_engine/recipes_registry.py
  - backend/src/task_center/context_engine/renderer.py
  - backend/src/task_center/context_engine/scope.py
  - backend/src/task_center/context_engine/recipes/__init__.py
  - backend/src/task_center/context_engine/recipes/_mission_episode.py
  - backend/src/task_center/context_engine/recipes/_summaries.py
  - backend/src/task_center/context_engine/recipes/attempt_landscape.py
  - backend/src/task_center/context_engine/recipes/entry_executor.py
  - backend/src/task_center/context_engine/recipes/evaluator.py
  - backend/src/task_center/context_engine/recipes/generator.py
  - backend/src/task_center/context_engine/recipes/helper.py
  - backend/src/task_center/context_engine/recipes/planner.py
  - backend/src/task_center/entry/__init__.py
  - backend/src/task_center/entry/controller.py
  - backend/src/task_center/entry/coordinator.py
  - backend/src/task_center/entry/sandbox_bridge.py
  - backend/src/task_center/episode/__init__.py
  - backend/src/task_center/episode/closure_report.py
  - backend/src/task_center/episode/episode.py
  - backend/src/task_center/episode/manager.py
  - backend/src/task_center/episode/registry.py
  - backend/src/task_center/episode/validation.py
  - backend/src/task_center/mission/__init__.py
  - backend/src/task_center/mission/ancestry.py
  - backend/src/task_center/mission/close_report_delivery.py
  - backend/src/task_center/mission/handler.py
  - backend/src/task_center/mission/mission.py
  - backend/src/task_center/mission/starter.py
  - backend/src/task_center/mission/validation.py
  - backend/src/task_center/task/__init__.py
  - backend/src/task_center/task/ids.py
  - backend/src/task_center/task/models.py
findings:
  critical: 2
  warning: 8
  info: 7
  total: 17
status: issues_found
---

# Code Review Report: `backend/src/task_center`

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 55
**Status:** issues_found

## Summary

The `task_center` package is the orchestration hub of EphemeralOS: missions create episodes, episodes manage attempts, attempts run planner → generator DAG → evaluator phases, and the context engine renders prompts for each role. The design is well-factored — the lifecycle owners (MissionHandler, EpisodeManager, AttemptOrchestrator, EntryTaskController) each own a single state-transition boundary, and the seams between them are crisp. Invariant checking is consistent and aggressive (every state read raises `TaskCenterInvariantViolation` rather than tolerate drift), which is the right choice for harness state.

Two issues rise to **BLOCKER**: (1) the agent runner's return value is dereferenced as `result.status` without a None-guard despite being typed `Any | None` and pre-initialized to `None` (`agent_launch/launcher.py:142`); and (2) `mission_id` flows from a `str | None` scope into `mission/ancestry.py` whose signature is `mission_id: str`, with no upstream guard, so a future variant declared on the entry-executor (whose scope has no `mission_id`) would crash inside `mission_store.get(None)`.

The remaining warnings are quality issues: silent context drops in recipes when a dependency task is missing, asserts used for production runtime invariants, an enum name/value mismatch (`WAITING_COMPLEX_TASK = "waiting_mission"`) that invites future drift, and a stale duplicate key in a metadata dict. Info items are minor.

The async surface area is narrow (only `agent_launch/launcher.py` handles tasks) and the pending-task tracking, while subtle, is correct. There are no security issues, no hardcoded secrets, no injection paths.

## Critical Issues

### CR-01: Unguarded `result.status` access can crash the launcher's asyncio task

**File:** `backend/src/task_center/agent_launch/launcher.py:115-146`
**Issue:** `_run_launch` initializes `result: Any | None = None`, calls `runner(...)` inside a `try`, and on success reads `result.status` at line 142. The `except Exception` branch returns explicitly (line 140), so on the success path `result` is whatever `runner` returned. The injected runner type is `Callable[..., Awaitable[Any]]` — any value is permitted, including `None`. The production runner (`engine.api.run_ephemeral_agent`) always returns an `EphemeralRunResult` with a `.status` attribute, but the launcher is a public injection seam (tests, alternative runners, future replacements). If any injected runner ever returns `None` or any object without `.status`, line 142 raises `AttributeError` *outside* the `try`. That uncaught error escapes `_run_launch`, the asyncio task ends with an exception, and the next call to `wait_for_idle()`'s `asyncio.gather(*pending)` will re-raise it — crashing whatever (entry coordinator, test harness) is awaiting idle.

The `result: Any | None = None` initialization at line 115 is itself a hint that the author considered the None case but did not propagate the check after the `try`.

**Fix:** Either tighten the runner Protocol to forbid `None` returns and assert post-call, or branch explicitly:

```python
result = await runner(
    self._config,
    launch.task_input,
    agent_def=agent_def,
    sandbox_id=self._sandbox_id,
    persist_agent_run=True,
    task_id=launch.task_id,
    on_event=self._on_event,
    extra_tool_metadata=metadata,
)
# Guard the public-seam contract: any runner that returns None is treated
# the same as a crash so wait_for_idle does not propagate AttributeError.
if result is None:
    await self._report_unfinished_running_task(
        launch,
        summary="Agent runner returned None.",
    )
    return

if result.status == "failed":
    summary = f"Agent run failed: {result.error or 'unknown error'}"
else:
    summary = "Agent run ended without a terminal submission."
await self._report_unfinished_running_task(launch, summary=summary)
```

Alternatively, type-narrow at the seam (`AttemptAgentRunner = Callable[..., Awaitable[EphemeralRunResult]]`) and remove the `Any | None` initial value.

---

### CR-02: `mission_id` flows from `str | None` scope into ancestry walker typed `str`

**File:** `backend/src/task_center/agent_launch/predicates.py:59-67`, `backend/src/task_center/mission/ancestry.py:16-23`
**Issue:** `_partial_plan_caller_ancestor` passes `ctx.scope.mission_id` (typed `str | None` per `ContextScope`) directly into `has_partial_planned_caller_ancestor(mission_id: str, ...)`. The predicate has no `None` guard. If any base agent's `variants:` list registers `partial_plan_caller_ancestor` and gets resolved against a scope without `mission_id` set, the walker is entered with `mission_id=None` and falls through to:

```python
seen_mission_ids.add(current_mission_id)   # set.add(None) — works
current_mission = mission_store.get(current_mission_id)  # depends on store
```

`mission_store.get(None)` would either return `None` (triggering a `TaskCenterInvariantViolation` for "missing mission") or, worse, behave undefined depending on the persistence layer. Today the only `ContextScope(task_id=...)` construction site without a `mission_id` is the entry executor (`entry/coordinator.py:303-307`), and the entry_executor agent has no `variants:` list — so this is latent rather than live. But the type system is silently approving an unsafe call: there is no `mypy` complaint because `predicates.py` lacks `from __future__ import annotations` evaluation that checks the narrowing.

This is the kind of bug that ships the first time someone declares a variant on `entry_executor`, or extends ContextScope semantics, or adds a new role with optional `mission_id`.

**Fix:** Either widen the ancestry signature and short-circuit on None, or have the predicate raise when called without a mission_id. The latter is more honest because "no mission" means "no partial-planned caller" only definitionally; explicit beats implicit:

```python
def _partial_plan_caller_ancestor(ctx: ResolverContext) -> bool:
    """Delegate to the canonical ancestry predicate."""
    mission_id = ctx.scope.mission_id
    if mission_id is None:
        # No mission scope => no caller-attempt ancestry to walk.
        return False
    return has_partial_planned_caller_ancestor(
        mission_id=mission_id,
        mission_store=ctx.deps.mission_store,
        episode_store=ctx.deps.episode_store,
        attempt_store=ctx.deps.attempt_store,
        task_store=ctx.deps.task_store,
    )
```

Or tighten the ancestry helper's signature to `mission_id: str | None` with an explicit early-return.

## Warnings

### WR-01: Silent `continue` drops dependency context blocks when a task row is missing

**File:** `backend/src/task_center/context_engine/recipes/generator.py:97-100`, `backend/src/task_center/context_engine/recipes/evaluator.py:66-69`
**Issue:** When building the LLM-facing context packet, both recipes loop over the assigned task's `needs` (generator) or the attempt's `generator_task_ids` (evaluator) and call `task_store.get_task(dep_id)`. If the store returns `None`, the recipe silently `continue`s — the LLM is then run with no dependency-summary block for that need. Every *other* "row not found" path in these recipes raises `ContextEngineError`. This is inconsistent: missing dependency rows mean the model is reasoning over an incomplete frame and the harness has no visibility into the omission.

For the generator recipe this is especially load-bearing: `needs` is the persisted DAG edge list. A missing dep there is an invariant violation (dependencies were validated when the planner submission was accepted at `ordered_generator_tasks`), not a tolerable absence.

**Fix:** Raise `ContextEngineError` consistent with the recipe's other not-found branches. Example for `generator.py`:

```python
def _dependency_summary_blocks(
    *,
    needs: tuple[str, ...],
    task_store: "TaskCenterStore",
) -> list[ContextBlock]:
    out: list[ContextBlock] = []
    for dep_id in needs:
        dep = task_store.get_task(dep_id)
        if dep is None:
            raise ContextEngineError(
                f"Dependency task {dep_id!r} referenced by needs is missing; "
                "generator context cannot be assembled without dependency results."
            )
        out.append(...)
```

For the evaluator's loop over `attempt.generator_task_ids`, the same applies — those are the planner-submitted DAG nodes that should always exist by the time the evaluator runs.

---

### WR-02: `assert` statements gate production invariants — stripped under `python -O`

**File:**
- `backend/src/task_center/agent_launch/launcher.py:225, 242, 260`
- `backend/src/task_center/context_engine/recipes/planner.py:40-42`
- `backend/src/task_center/context_engine/recipes/generator.py:35-37`
- `backend/src/task_center/context_engine/recipes/evaluator.py:33-34`
- `backend/src/task_center/context_engine/recipes/entry_executor.py:29`
- `backend/src/task_center/context_engine/recipes/helper.py:53-55`
- `backend/src/task_center/mission/handler.py:141`
- `backend/src/task_center/mission/starter.py:105`

**Issue:** Python's `assert` statements are removed when the interpreter runs with `-O`. The recipes use them as "scope already validated by `assert_fields`" type narrowing — that's correct as a narrowing hint, but downstream attribute access (`mission_store.get(scope.mission_id)`) then passes `None | str` to a function that types it as `str`. Type narrowing is a property of the static type checker; the runtime check disappears under `-O`. If anyone ever runs the harness under `-O` (uncommon but possible in optimized container images), every recipe's `assert scope.mission_id is not None` becomes a no-op and a malformed scope flows further before tripping a downstream `None` check.

The launcher's `assert launch.attempt_id is not None` on lines 225/242/260 is callsite-internal: the static methods are only invoked from a branch that just verified `launch.attempt_id is None` returns. Safer code makes that contract explicit at the static method boundary.

**Fix:** Replace asserts with explicit `if X is None: raise TaskCenterInvariantViolation(...)`, or use `cast` after the `assert_fields` call to narrow types statically without a runtime no-op. For the recipes, the engine already calls `scope.assert_fields(recipe.required_scope_fields)` before invoking the recipe — so the recipe `assert`s are pure type-checker hints. They can be `assert scope.mission_id is not None  # noqa: S101` documented as such, or replaced with `cast(str, scope.mission_id)`.

---

### WR-03: `HarnessTaskStatus.WAITING_COMPLEX_TASK` enum name and value diverge

**File:** `backend/src/task_center/task/models.py:19`
**Issue:** The enum member is named `WAITING_COMPLEX_TASK` but its string value is `"waiting_mission"`. Every comparison site uses `.value` against the persisted string, so today this works, but the divergence violates the principle of least surprise:
- Anyone debugging logs sees `"waiting_mission"` and grepping for `WAITING_COMPLEX_TASK` finds the enum but not the string.
- Anyone serializing the enum by name (e.g., `status.name` in a log line) emits `WAITING_COMPLEX_TASK`, breaking any downstream that expects `"waiting_mission"`.
- A future refactor renaming one without the other silently breaks the contract.

This is a future-bug magnet, not a current bug. Recommend aligning the name and value:

**Fix:** Either rename the enum to `WAITING_MISSION = "waiting_mission"` (preferred — "complex_task" is an outdated term, every comment and payload field now uses "mission") or rename the value to `"waiting_complex_task"` and migrate persisted data. The former is safer in this codebase given the existing string-value usage.

---

### WR-04: `_dispatch_generating` launch-failed flag uses brittle boolean composition

**File:** `backend/src/task_center/attempt/dispatcher.py:101-109`
**Issue:** The expression

```python
launch_failed = (
    not self._launch_ready_generator(
        attempt=attempt,
        task_id=task_id,
    )
    or launch_failed
)
```

is correct (Python's short-circuit `or` returning the right operand when the left is falsy), but reads as if it overwrites the running flag. The intent — "remember that *any* launch failed across this loop" — is more clearly expressed as:

```python
if not self._launch_ready_generator(attempt=attempt, task_id=task_id):
    launch_failed = True
```

or

```python
launch_failed = launch_failed or not self._launch_ready_generator(...)
```

The current form invites a future "fix" that flips it to `launch_failed = launch_failed or not ...` and accidentally changes behavior.

**Fix:** Convert to the explicit form shown above. Behavior is preserved.

---

### WR-05: `_compensate_failed_start` may leave parent task in `WAITING_COMPLEX_TASK` after compensation

**File:** `backend/src/task_center/mission/starter.py:218-267`
**Issue:** The compensation flow runs (1) close attempt, (2) cancel episode, (3) cancel mission, (4) rollback parent task status, (5) deregister episode manager. Each step is wrapped in `try/except Exception` with logging; step 4 logs `critical` if it fails. But the rollback at step 4 uses `set_task_status_if_current(expected=WAITING_COMPLEX_TASK, status=RUNNING)`, and if the task was *never* marked WAITING (the `start` body raised before `_mark_parent_waiting`), the CAS returns None and step 4 is a no-op — which is correct. But if `start_attempt` raised (line 93) *after* the parent was marked waiting, and the rollback CAS fails (e.g., race or store error), the parent task stays in `WAITING_COMPLEX_TASK` forever and the harness has no driver to reset it. The `logger.critical` message tells operators about the orphan, but there is no automated recovery path.

This is an operational concern, not a correctness bug — but the critical-log+manual-recovery contract should be reflected in a defensive read: if the parent task is in `WAITING_COMPLEX_TASK` and the mission row is `CANCELLED` (which compensation just did), the close-report router should still be able to deliver a synthetic `MissionCloseReport` with `outcome="failed"` to unstick the parent. Currently `close_report_delivery.py:60-64` raises `TaskCenterInvariantViolation` when the task is anything other than `WAITING_COMPLEX_TASK` or terminal, so the synthetic-failure delivery path would work, but `close_mission` is never called for cancelled-by-compensation missions — `cancel_for_compensation` does not emit a close report.

**Fix:** When compensation cannot roll the parent back, consider emitting a synthetic `MissionCloseReport(outcome="failed")` so the existing close-report router can unstick the parent task and finalize the run. At minimum, document the contract that `WAITING_COMPLEX_TASK` is a recoverable state only while the mission row is open, so operators have a checklist.

---

### WR-06: `MissionStarter._build_handler` builds a `MissionHandler` lazily but the orchestrator factory closes over `self._runtime` at construction time

**File:** `backend/src/task_center/mission/starter.py:117-142`
**Issue:** `_build_handler` is invoked on every `start()` call; it caches `self._handler` after first build. The cached handler holds `orchestrator_factory = make_attempt_orchestrator_factory(runtime=self._runtime)` — captured from `self._runtime` at first-call time. If `_runtime` is ever replaced or mutated between calls (`AttemptRuntime` is frozen, so the *instance* won't change, but `composer` and `entry_task_controller` fields on the runtime are settable post-construction in tests), the cached factory closure may not see those updates.

Today `AttemptRuntime` is `@dataclass(frozen=True, slots=True)` so the fields cannot be reassigned. So this is a non-issue *unless* the runtime ever becomes non-frozen. Flagging because the lazy-build + closure-capture pattern is subtle and the freezing is what makes it safe.

**Fix:** Either document the dependency on `AttemptRuntime` being frozen (one-line comment in `_build_handler`), or build the factory eagerly during `MissionStarter.__init__` so the capture point is obvious.

---

### WR-07: `entry/coordinator.py:227-249` registers builtin predicates/recipes on every entry-coordinator start

**File:** `backend/src/task_center/entry/coordinator.py:228-247`
**Issue:** `_build_composer` calls `register_builtin_predicates()` and `register_builtin_recipes()`. Both are documented as idempotent — `RecipeRegistry.register` overwrites the dict entry with the same recipe. But they also call `validate_agent_definitions_resolved()` on every start. In a long-running server that handles many top-level requests, that's repeated work plus the side effect of clobbering test-injected predicates/recipes that may have been installed earlier (e.g., a test registered an ad-hoc predicate, then a subsequent `start_task_center_entry_run` runs in the same process and overwrites — or worse, the test's predicate is overwritten by the builtin).

The "idempotent — safe to call repeatedly" docstring assumes nobody is registering between calls. That's a runtime contract worth checking: the `PredicateRegistry._registry` and `RecipeRegistry._registry` are class-level dicts shared across the process. Once a single test in a session calls `clear()` to start fresh, subsequent registrations stay. If two entry coordinators race, both call register_builtin, both call `validate_agent_definitions_resolved` — no race because both write the same values.

**Fix:** Either move builtin registration to a once-per-process bootstrap (a guard flag), or document that re-registration is the intended steady-state behavior and tests that need clean registries should use a teardown.

---

### WR-08: `MarkdownPromptRenderer._compress` mutates the in-place list it returns; recipes whose `blocks` list is reused will see truncated text

**File:** `backend/src/task_center/context_engine/renderer.py:201-235`
**Issue:** `_compress` is called with `packet.blocks`. The first line is `kept = list(blocks)` — a shallow copy. Inside the loop, `kept[idx] = replacement` replaces references in the copy. So the *list* is independent of `packet.blocks`. **However**, `_render_blocks` is called via `self._render_blocks(helper_owned)` where `helper_owned` is filtered from `kept`. Since `kept` already shallow-copied `packet.blocks`, the renderer never mutates the packet's underlying list. Good.

But the truncation replaces the block via `block.model_copy(update={"text": text})` which returns a *new* Pydantic model. The original packet's block stays untouched. The renderer is pure with respect to the packet. Confirmed safe.

**Reduce to INFO:** This is fine. Moving to info as IN-07 since the budgeted truncation has one subtle property worth documenting — see info section.

**Actual warning:** `_estimate_tokens` is called every loop iteration on the (potentially long) `block.text`. For a 100-block packet with very large texts and a tight budget, this is O(N^2). Performance is explicitly out of v1 scope per the review brief, so this is **not** flagged. Listing here only to acknowledge the review caught it and intentionally excluded it.

**No fix required** — leaving WR-08 as a placeholder for transparency. (See IN-07 for the surviving documentation point.)

## Info

### IN-01: Redundant `episode_sequence_no` key in `_mission_episode.py` metadata dicts

**File:** `backend/src/task_center/context_engine/recipes/_mission_episode.py:88-115`
**Issue:** The metadata dict is built as `{**base_meta, "episode_sequence_no": str(prior.sequence_no), "subheading": ...}` where `base_meta` already contains `"episode_sequence_no": str(prior.sequence_no)`. The spread + override writes the same value twice. The dict ends up with one entry (Python dict semantics: later keys overwrite), so behavior is correct, but the duplication is dead code.

**Fix:** Drop the explicit second `"episode_sequence_no"`:

```python
metadata={
    **base_meta,
    "subheading": f"Episode {prior.sequence_no} accepted plan",
},
```

---

### IN-02: `_select_variant_target` has untyped `variant` parameter with `# type: ignore`

**File:** `backend/src/task_center/agent_launch/resolver.py:94`
**Issue:** `def _select_variant_target(self, variant) -> AgentSelection:  # type: ignore[no-untyped-def]`. The `variant` parameter is an `AgentVariantBlock` (or whatever the agents module exposes). Annotating it removes the `type: ignore` and gives mypy a chance to catch attribute typos (`variant.use`, `variant.required_context_blocks`, `variant.note`).

**Fix:** Import the variant type from `agents` and annotate.

---

### IN-03: `MissionStarter._assert_parent_running_and_no_open_child` whitespace check is dead code

**File:** `backend/src/task_center/mission/starter.py:62-64`
**Issue:** `if not task_center_run_id or task_center_run_id.isspace():`. `task_center_run_id` came from `str(parent_task.get("task_center_run_id") or "")` at line 62 — that already coerced None to empty string. `"".isspace()` returns `False`, so the `isspace()` branch only fires for strings of one-or-more whitespace characters. If the intent was "reject empty and whitespace-only", `task_center_run_id.strip() == ""` would be one expression covering both. As written, an empty string is caught by `not task_center_run_id`, and a whitespace-only string is caught by `isspace()` — so the check is correct but redundantly composed.

**Fix:** `if not task_center_run_id.strip():`.

---

### IN-04: Class-level mutable `_registry` dicts on `PredicateRegistry` and `RecipeRegistry`

**File:** `backend/src/task_center/agent_launch/predicates.py:34`, `backend/src/task_center/context_engine/recipes_registry.py:40`
**Issue:** Both registries hold their state in class-level mutable dicts. The docstrings say "process-global" and tests call `clear()`. This is intentional, but worth noting because the pattern often signals an unintended singleton. The TaskCenter codebase has at least one other pattern of registries (`AttemptOrchestratorRegistry`, `EpisodeManagerRegistry`) that are instance-based. For consistency, future refactors could move `PredicateRegistry` and `RecipeRegistry` to instance state held on `ContextEngineDeps` so test isolation is automatic. Not a defect today.

**Fix:** Optional. Consider migrating to instance-based registries when the next breaking change in this area lands.

---

### IN-05: `AttemptRuntime.composer` and `entry_task_controller` default to `None` "so existing tests can continue"

**File:** `backend/src/task_center/attempt/runtime.py:55-63`
**Issue:** The dataclass docstring openly states these defaults exist for test compatibility. Carrying test seams into production data classes is a smell — every production caller must either trust `require_composer()` to raise or guard against `None`. Cleaner: require them at runtime construction and have tests build a minimal-but-real composer. Today the `None` defaults are isolated to a `require_composer()` lookup that raises `TaskCenterInvariantViolation`, so production safety is preserved.

**Fix:** Optional. When test infrastructure permits, drop the `None` defaults and force test fixtures to supply a stub.

---

### IN-06: Duplicate `MissionCloseReport` import paths across the package

**File:** `backend/src/task_center/api.py`, `backend/src/task_center/attempt/orchestrator.py:10`, `backend/src/task_center/mission/close_report_delivery.py:18`, etc.
**Issue:** `MissionCloseReport` is imported from `task_center.mission.mission` everywhere except the public `api.py` re-export. The package's `domain.py` re-exports it too. There are at least three valid import paths (`from task_center.mission.mission import MissionCloseReport`, `from task_center.domain import MissionCloseReport`, `from task_center.api import MissionCloseReport`). Internal callers use the first; external callers use the third. The domain.py re-export sits in the middle. Documenting an import-path policy ("internal: `mission.mission`, external: `api`") in a top-level docstring or in the `__init__.py` would prevent drift.

**Fix:** Optional. Document the import policy in `task_center/__init__.py` or a CONTRIBUTING note.

---

### IN-07: `MarkdownPromptRenderer._compress` is pure with respect to `packet.blocks` — worth a comment

**File:** `backend/src/task_center/context_engine/renderer.py:201-235`
**Issue:** `_compress` does `kept = list(blocks)` (shallow copy) and then `kept[idx] = self._truncate(block)` where `_truncate` returns a new model via `model_copy`. The original `packet.blocks` is never mutated — the renderer is pure with respect to the input packet. This is the *correct* behavior given the packet was just persisted via `context_packet_store.insert` in `ContextComposer.compose`, but the purity is implicit. A one-line docstring noting "_compress returns a new list of (possibly truncated) blocks; the input list is not mutated" would make the contract explicit and prevent a future refactor from silently changing it.

**Fix:** Add a one-line docstring to `_compress`.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
