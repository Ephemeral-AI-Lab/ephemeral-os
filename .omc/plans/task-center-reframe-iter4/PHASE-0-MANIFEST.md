# Phase 0 — Manifest (task_center reframe iter4)

**Date:** 2026-05-14
**Branch:** `codex/fix-dot-path-normalization-tests`
**Plan:** `.omc/plans/task-center-folder-reframe-20260514.md`
**Execution mode:** inline, no subagents
**Measurement mode:** `wc -l` (cloc not installed; user accepted approximation per session start)

---

## Baseline

| Metric | Value |
|---|---|
| File count (`*.py`, excluding `__pycache__`) | **62** |
| wc-LoC total | **7,613** |
| cloc-code (claimed in plan) | 5,443 |
| Top-5 files (wc-LoC) | orchestrator.py 422, launcher.py 375, starter.py 367, coordinator.py 346, dispatcher.py 318 |
| Doctest hits in `task_center/` | **0** (no doctests — docstring sweep safe) |
| `which pytest` | `/opt/homebrew/bin/pytest` (global) — must use `.venv/bin/pytest` per `feedback_use_venv_pytest` |
| `.venv/bin/pytest` | present |
| PEP 420 namespace verification | **OK** (`task_center.attempt.__file__` and `task_center.mission.__file__` both non-None) |

---

## Pre-Phase-1 Grep Gates

### Pure-deletion levers

| Lever | Target | Grep | Hits | Verdict |
|---|---|---|---|---|
| #1 | `events.py` deletion | `rg "from task_center\.events\|import task_center\.events" backend/` | **0** | ✅ GREEN — safe to delete |
| #2 | 7 typed-payload classes in `task_state.py` | `rg "\.to_dict\(\)" backend/src/task_center/` | **2 (both in docstring/comment of task_state.py itself)** | ✅ GREEN — no live callers |
| #10 | `RegisteredEpisodeManager` Protocol | `rg "RegisteredEpisodeManager" backend/` | **7 (all internal: protocols.py 2, episode/registry.py 5)** | ✅ GREEN — internal-only |
| **#14a** | `ContextPacketStoreProtocol` + `context_packet_store` field | `rg "\bContextPacketStoreProtocol\b" backend/` AND `rg "context_packet_store" backend/` | Protocol: **2 (engine.py only)**. Field consumers: **`tools/ask_helper/_lib/_compose.py` (production), `conftest.py` (sets field), 6+ live_e2e/test sites** | ❌ **RED — ABORTED.** Plan's claim "field never set" is false. `deps.context_packet_store.get(...)` is load-bearing in `tools/ask_helper`. |
| #14b | `ContextScope.for_helper` | `rg "for_helper" backend/` | **1 (declaration only in scope.py)** | ✅ GREEN — safe to delete |
| #18 | 4 audit payload classes | `rg "TaskReadyPayload\|TaskLaunchedPayload\|TaskFailedPayload\|_BaseTaskPayload" backend/` | **7 (all self-hits in audit.py: 4 decls + 3 `__all__`)** | ✅ GREEN — pure dead |

### Behavioral-inlining lever consumer counts

| Lever | Target | Grep | Hits | Verdict |
|---|---|---|---|---|
| #15 | `LaunchBuilder.for_planner/for_generator/for_evaluator/for_entry` callsites | `rg "for_planner\(\|for_generator\(\|for_evaluator\(\|for_entry\(" backend/` | **8 (4 callsites: orchestrator.py, dispatcher.py×2, coordinator.py + 4 declarations in launch_builder.py)**. Note: `ContextScope.for_planner/for_generator/for_evaluator` also exist in `scope.py` — these are NOT in scope of #15. | ✅ Matches plan |
| #16a | `AgentResolver` | `rg "AgentResolver" backend/` | 1 Protocol decl + 1 impl (`RuleBasedAgentResolver`) + 2 refs in composer.py — all internal | ✅ GREEN |
| #16b | `PromptRenderer` | `rg "PromptRenderer" backend/` | 1 Protocol decl + 1 impl (`MarkdownPromptRenderer`) + 2 refs in composer.py — all internal | ✅ GREEN |
| #16c | `AttemptAgentLauncher` | `rg "AttemptAgentLauncher" backend/` | 1 Protocol decl in runtime.py + 1 impl (`EphemeralAttemptAgentLauncher`) + multiple refs in launcher.py + contexts.py + 2 string-literal mentions in live_e2e comments | ✅ GREEN (live_e2e refs are docstring comments, not imports) |

### External-grep gate (NG-3 protected directories)

`rg "from task_center\." backend/src/db/ backend/src/tools/ backend/src/live_e2e/ backend/src/agents/`

Hits found:
- `db/stores/mission_store.py: from task_center.mission.state import ...`
- `db/stores/context_packet_store.py: from task_center.context_engine.packet import ContextPacket`
- `db/stores/episode_store.py: from task_center.episode.state import ...`
- `db/stores/attempt_store.py: from task_center.attempt.state import ...`
- `live_e2e/squad/runner.py: from task_center.attempt import Attempt`
- `live_e2e/squad/runner.py: from task_center.episode.state import Episode`

**Public deep-import paths to preserve (PIN):**
- `task_center.mission.state.*`
- `task_center.context_engine.packet.ContextPacket`
- `task_center.episode.state.*` (Episode + others)
- `task_center.attempt.state.*` (Attempt + others)
- `task_center.attempt` (root) — Attempt re-export via `attempt/__init__.py`

**Implication for Phase 7 `__init__.py` deletion:** S9 mitigation in plan stands — keep `attempt/__init__.py` as 1-line re-export shell (final file count drifts from 31 → 33; user-acknowledged overshoot path).

---

## Roll-up impact of Lever #14a abort

| Lever | Pre-abort cloc-code | Post-abort cloc-code |
|---|---|---|
| #14a (ContextPacketStoreProtocol + field) | 20 | **0 (ABORTED)** |

| Metric | Pre-abort | Post-abort | Floor |
|---|---|---|---|
| Conservative roll-up | 885 | **865** | 850 (soft) |
| Buffer above soft floor | +35 | **+15** | — |
| Mid-band roll-up | 1,151 | **1,131** | 1,089 (target) |
| Buffer above target | +62 | **+42** | — |
| Above hard abort (850) | yes | yes | — |

**Decision:** Proceed. Conservative case still clears soft floor by 15 cloc-code; mid-band still clears 1,089 target.

---

## Phase 0 Spike — modified scope

Original plan: spike on Lever #1 + #14a + #14b + #18 → measure cloc-code delta vs 215 conservative band.

**Modified:** Lever #14a aborted. Revised spike: **#1 + #14b + #18 only**. Conservative cloc-code band: **160 (215 minus 20 for #14a, minus ~35 already in #14a's share of the 215)**. wc-LoC band (approximation): ~180.

**Decision:** Spike folded into Phase 1 directly (no throwaway branch). Phase 1's regression gate (pytest + wc-LoC delta) serves the spike's purpose. If Phase 1 delta < conservative wc-LoC band, halt and re-iterate per plan §Phase 0 step.

---

## Open Items Surfaced to User

1. **Lever #14a is aborted.** Plan needs amendment for the §Investigation Summary roll-up. Recorded above.
2. **cloc binary missing.** AC #5 originally required `cloc --quiet` measurement. User accepted `wc -l` approximation; AC #5 cloc-code numbers in this run are **approximations**, not authoritative. If user later wants strict AC #5, install cloc + re-measure pre/post.
3. **`attempt/__init__.py` re-export shell must be preserved.** Confirmed via external grep — `live_e2e/squad/runner.py:33` imports `from task_center.attempt import Attempt`. File-count target moves from 31 → 33 unless user overrides NG-3 to edit live_e2e.

---

## Baseline pytest run

| Suite | Result | Duration |
|---|---|---|
| `.venv/bin/pytest backend/tests/unit_test/test_task_center/ -q -x` | **239 passed** | 1.63s |

Clean green baseline. No xfails, no skips reported by `-q`.

---

## Phase 1 — Pure Deletions (executed 2026-05-14)

| Lever | Commit | Attribution | wc-LoC delta | Plan target | Notes |
|---|---|---|---|---|---|
| #1 (events.py) | swept into `b3c6fe8f` (codex parallel) | drift | -110 | ~110 wc | First commit lost attribution race. Code change landed. |
| #2 (task_state.py payloads) | `774425c1` | mine + swept layer_stack files | -127 | 100-110 wc | Beat plan estimate by ~17 LoC. |
| #10 (RegisteredEpisodeManager) | `b0d0ad74` | mine only | -19 | ~15 wc | Clean. |
| #14a (ContextPacketStoreProtocol) | **ABORTED** | n/a | 0 | 24 wc | Field load-bearing in `tools/ask_helper`. |
| #14b (for_helper) | `b1510e05` | mine only | -17 | ~8 wc | Beat plan estimate. |
| #18 (audit payloads + test) | `3a7b5896` | mine only | -38 net (-53 src, +15 test cleanup) + 83 test file | 60-100 wc | Plan over-estimated; emit sites already used inline dicts. Added 3-assert regression test (`test_audit/test_emission_shape.py`). |

**Phase 1 totals:**
- File count: 62 → **61** (events.py removed)
- wc-LoC: 7,613 → **7,289** (delta **-324**)
- Tests: 239 → **242** passing (3 new regression tests)
- Plan target: ≥270 wc-LoC removed → **actual -324, beats target by +54** ✅
- Conservative cloc-code estimate (assuming 75% code, 25% comment): ~-243 (close to plan's 270 target)
- Optimistic cloc-code estimate (assuming 85% code on dataclass-heavy deletions): ~-275 (clears plan target)

**Phase 1 deviations:**
- Lever #14a aborted (recorded above). Roll-up impact: conservative 885 → 865 cloc-code; mid-band 1,151 → 1,131. Still above iter5 soft floor (850).
- Lever #18 test placed in `test_audit/` (existing concern subdir) rather than `test_lifecycle/` (plan's named target).
- 3 of 5 commits cleanly attributed; 2 swept by parallel codex (#1 entirely, #2 partially) — code changes intact, git provenance noisy.

**Phase 1 verification gate:** ✅ pytest green (242 passed in 1.60s) — Phase 2 unblocked.

---

## Phase 2 — `_core/` relocation (executed 2026-05-14)

7 files moved into `task_center/_core/` with 1-line star-import shims at original paths (lever #21 pre-stage). `_EXPORTS["TaskCenterInvariantViolation"]` re-pointed.

| Original | New home | Consumers | Shim? |
|---|---|---|---|
| `exceptions.py` | `_core/exceptions.py` | 29 internal, 0 external | ✓ |
| `task_ids.py` | `_core/ids.py` (renamed) | 12 internal, 0 external | ✓ (kept old `task_ids.py` name) |
| `config.py` | `_core/config.py` | 10 internal, 0 external | ✓ |
| `audit.py` | `_core/audit.py` | 4 internal, 0 external | ✓ |
| `invariants.py` | `_core/invariants.py` | 8 internal, 0 external | ✓ |
| `persistence.py` | `_core/persistence.py` | 24 internal, 0 external | ✓ |
| `protocols.py` | `_core/protocols.py` | 3 internal, 0 external | ✓ |

**Commit:** `23e245a3` (16 files, mine only — clean attribution)
**Tests:** 242 task_center + 140 cross-package pass (382 total)
**Phase 2 cloc-code delta:** 0 (relocation only; savings in Phase 7 shim collapse)
**File count delta:** 61 → 69 (added _core/{__init__,7 files} = 8)

---

## Phase 3 — Domain-folder relocations (executed 2026-05-14)

5 root domain modules moved into their owning sub-packages with 1-line shims.

| Original | New home | Consumers |
|---|---|---|
| `contexts.py` | `attempt/contexts.py` | 3 internal, 0 external |
| `lifecycle.py` | `attempt/lifecycle.py` | 3 internal, 0 external |
| `launcher.py` | `attempt/launcher.py` | 2 internal, 0 external |
| `launch_builder.py` | `attempt/launch_builder.py` | 3 internal, 0 external |
| `saga.py` | `mission/saga.py` | 1 internal, 0 external |

**Commit:** my shims landed in `1dca206f` (5 files); the 5 actual renames were swept into codex's `e3856f1b W2: command_exec/ -> execution/` commit (attribution drift again — code change intact, git provenance noisy).
**Tests:** 414 passing (task_center + tools + agents). `live_e2e` import smoke OK.
**Phase 3 cloc-code delta:** 0 (relocation only)
**File count delta:** 69 → 74 (added 5 new shims at root)

---

## Cumulative status after Phase 3

| Metric | Baseline | Current | Δ |
|---|---|---|---|
| File count | 62 | **74** | +12 (shims; will collapse in Phase 7 → target 31) |
| wc-LoC | 7,613 | **7,326** | **-287** (Phase 1: -324; Phase 2+3 shim overhead: +37) |
| Tests | 239 | **242** | +3 regression tests, all green |

---

## Phase 4a — Mission handler family merger (executed 2026-05-14)

Merged `mission/episode_factory.py` (130 wc) + `mission/episode_closure_router.py` (145 wc) + `mission/handler.py` (144 wc) → single `mission/handler.py` at **exactly 300 wc-LoC** (Phase 4a ceiling).

**Strategy:** class-preserving merger (3 classes in 1 file) rather than method-inlining. Deduplicated imports, condensed module/class docstrings, dropped unused `logger`, compressed `__all__` to one line, dropped non-essential inline comments.

**Commit:** `b9271ab5` (4 files, mine only — clean attribution)
**Test:** New `test_lifecycle/test_mission_handler_merged_dependencies_isolated.py` — 6 asserts (3 classes co-located, public-signature preserved, factory/router surface preserved, sink alias exists, carved-out files gone, LoC ≤480).
**Tests:** 388 passing (242 + 6 new).
**Phase 4a wc-LoC delta:** -119 (300 net vs 419 raw).

---

## Phase 4b — DTO mergers + close-report router rename (executed 2026-05-14)

Episode side:
- 6 closure DTOs absorbed into `episode/state.py` (`AttemptedPlanEntry`, `TerminalSuccess`, `SuccessContinue`, `AttemptPlanFailed`, `ClosureOutcome`, `EpisodeClosureReport`).
- `episode/closure_report.py` kept as a 1-line shim re-exporting from `state` (5 consumers unchanged; shim collapses in Phase 7).

Mission side:
- 2 DTOs (`CloseReportDeliveryStatus` + `CloseReportDeliveryResult`) moved from `close_report_delivery.py` into `mission/state.py`.
- `close_report_delivery.py` renamed to `close_report_router.py` (router class only).
- 2 consumers updated directly: `mission/starter.py` + `test_phase04_close_report_delivery.py`.

**Commits:** `722ac56e` (6 files) + `e205da6e` (cleanup, lands the rename's deletion side).
**Tests:** 420 passing across task_center+tools+agents.
**Phase 4b wc-LoC delta:** -5 net (mostly file rename + DTO relocation).

---

## Cumulative status after Phase 4

| Metric | Baseline | After Phase 3 | After Phase 4 | Δ from baseline |
|---|---|---|---|---|
| File count | 62 | 74 | **72** | +10 (shims remain) |
| wc-LoC | 7,613 | 7,326 | **7,208** | **-405** |
| Tests | 239 | 242 | **248** (task_center) / 420 (all) | +9 |

**Phase 4 cloc-code approximation (wc-LoC × 0.75 ratio):** ~-94 (lever #3 plan target: 95-125 — within band).

---

## Phase 5 — Inlinings (partial: 6 of 10 sub-levers, executed 2026-05-14)

Executed in ascending complexity order. All landed clean with regression tests where mandated.

| Sub | Lever | Description | Commit | wc-LoC Δ | Test |
|---|---|---|---|---|---|
| 5g | #17 | Inline `EpisodeFactory.has_orchestrator_factory` (1 property + 1 callsite) | `de627f0a` | -4 | regression test updated |
| 5b | #7 | Compact `attempt/stage_strategy.py` → dict-dispatch in `dispatcher.py` | `e8130e4e` | -41 | `test_stage_dispatch.py` (1 assert) |
| 5i | #5 | Inline `Registry[T]` base into PredicateRegistry + RecipeRegistry | `855543d6` | -28 (source) | `test_registry_inline_surface.py` (5 asserts) |
| 5h | #24 | Episode `_orchestrator_factory` guard inlines | **ABORTED** | 0 | Plan over-estimated; only 1 truthy guard, already load-bearing |
| 5a | #9 | Collapse `PlannerCtx`≡`GeneratorCtx` → `AttemptStageCtx`; `MissionLifecycleCtx` extends `EpisodeLifecycleCtx` | `0a84e4f5` | -39 (source) | `test_contexts_protocol_collapse.py` (4 asserts) |
| 5f | #16a/b/c | Delete `AgentResolver`, `PromptRenderer`, `AttemptAgentLauncher` Protocols → concrete refs | `17629259` | -38 (source) | `test_concrete_class_annotations.py` (4 asserts) |

**Phase 5 partial subtotal:** -150 src wc-LoC (matches plan estimate range 115-185 cloc-code for these 6 sub-levers).

### Phase 5 — final 4 sub-levers (executed 2026-05-14)

| Sub | Lever | Description | Commit | wc-LoC Δ | Test |
|---|---|---|---|---|---|
| 5c | #8 | Invariants compression (one-line raises, drop section dividers; **not** inlining — abort-band-limited) | `b2bb68f2` | -52 | existing tests cover |
| 5d | #6 | Launcher exhaustion: 4 `_report_*_exhaustion` + dispatch table → 1 `_report_exhaustion` | `2f618275` | -39 | `test_launcher_exhaustion_parametrized.py` (4 asserts) |
| 5e | #15 | LaunchBuilder: 4 factory methods kept (signatures diverged too much); extracted `_build` shared helper | `e81473ef` | -11 | `test_launch_builder_for_role.py` (6 asserts) |
| 5j | #4 | Saga inline: 105 wc-LoC `Saga` module deleted; replaced with local `_do(step_name, action)` in `MissionStarter._compensate_failed_start` | `6b8fb4e0` | -55 (incl. shim) | `test_saga_inline_equivalence.py` (3 asserts) |

**Phase 5 final subtotal:** -157 src wc-LoC across the 4 closing sub-levers (sum of -52, -39, -11, -55).

### Phase 5 — full grand total

| Sub | Lever | Status | wc-LoC Δ |
|---|---|---|---|
| 5g | #17 | done | -4 |
| 5b | #7 | done | -41 |
| 5i | #5 | done | -28 |
| 5h | #24 | **ABORTED** (plan over-estimated) | 0 |
| 5a | #9 | done | -39 |
| 5f | #16 a/b/c | done | -38 |
| 5c | #8 | done | -52 |
| 5d | #6 | done | -39 |
| 5e | #15 | done | -11 |
| 5j | #4 | done | -55 |
| **Total** | — | 9 active + 1 aborted | **-307** |

### Phase 5 deviations from plan

1. **Lever #24 aborted** — only 1 truthy guard available; already load-bearing. Plan estimate (5-10 cloc-code) was speculative.
2. **Lever #8 narrowed scope** — abort band ("any inline >2 lines at call site") ruled out aggressive inlining (most asserts have 2-5 call sites with multi-line raises). Switched to compress-only: single-line raises + drop section dividers. Saved -52 wc-LoC vs plan target 45-70 cloc-code.
3. **Lever #15 narrowed scope** — full `for_role(...)` consolidation was non-viable (4 signatures diverge too much for clean unification). Switched to safer pattern: keep 4 named methods, extract `_build` shared helper. Saved -11 wc-LoC vs plan target 55-80 cloc-code.
4. **Lever #4 inlined cleanly** — `Saga` (105 wc-LoC module + 3 LoC shim) → local `_do` closure in `MissionStarter._compensate_failed_start`. Inlined block ~25 LoC, well under the 60 LoC abort cap.

---

## Cumulative status after Phase 5 (complete)

| Metric | Baseline | After Phase 4 | After Phase 5 | Δ from baseline |
|---|---|---|---|---|
| File count | 62 | 72 | **68** | +6 (Phase 2 + 3 shims still pending Phase 7 collapse; offset by -4 deletions: events.py, stage_strategy.py, registry.py, saga.py + shim) |
| wc-LoC | 7,613 | 7,208 | **6,848** | **-765** (~10% reduction) |
| Tests (task_center) | 239 | 248 | **275** | +36 (incl. 27 new regression tests) |

---

## Phase 6 — Docstring sweep (SKIPPED per plan)

Plan §Phase 6: "NOT counted toward cloc-code gate." Skipping; review-only benefit not pursued in this session.

---

## Phase 7 — File-count consolidation (partial: 3 of 10 sub-steps, executed 2026-05-14)

| Sub | Description | Files removed | wc-LoC Δ | Notes |
|---|---|---|---|---|
| 7a | `_core/types.py` bundle (exceptions+ids+config+protocols) — persistence kept separate per iter4 amendment | -3 (deleted 4 `_core/foo.py`, added 1 `types.py`) | -2 net | 4 root shims re-pointed; `_EXPORTS["TaskCenterInvariantViolation"]` re-pointed |
| 7b | `_core/infra.py` bundle (audit + invariants) | -1 (deleted 2, added 1) | -9 | Final 291 LoC, under ≤300 ceiling |
| 7c | `mission/handler.py` absorbs `repository.py` + `ancestry.py` | -2 | -64 | Final 420 LoC, under ≤480 ceiling. 2 ancestry consumers updated (predicates + test) |

**Phase 7 partial subtotal:** -6 files, -75 wc-LoC.

### Phase 7d/7e (executed 2026-05-14)

| Sub | Description | Commit | Files Δ | wc-LoC Δ |
|---|---|---|---|---|
| 7d | `attempt/launch.py` merger — `launcher.py` (336) + `launch_builder.py` (148) → `launch.py` (468 LoC, under ≤480 ceiling). 2 internal consumers + 2 root shims + 3 tests updated. | (commit in branch log) | -1 | -16 |
| 7e | `attempt/runtime.py` absorbs `lifecycle.py` — `LifecycleTarget` Protocol + `GeneratorTaskLifecycle` class merged into `runtime.py`. Root shim `task_center/lifecycle.py` re-pointed at `attempt.runtime`. | `0f621858` | -1 | -41 |
| 7g-pre | Collapse `episode/closure_report.py` shim into `episode/state.py` — 5 consumers (handler, manager, 3 tests) updated. | `703ee389` | -1 | -10 |

### Remaining Phase 7 sub-steps (5 of 10)

| Sub | Description | Notes |
|---|---|---|
| 7f | `agent_routing/__init__.py` merger (predicates + resolver) | 15+ consumers — high-touch, deferred |
| 7g | Full `episode/__init__.py` bundle (state + manager + registry) | 30+ consumers — biggest substep |
| 7h | `entry/__init__.py` bundle (controller + coordinator + sandbox_bridge) | Abort if >600 LoC |
| 7i | `context_engine/core.py` (engine + composer + errors) | 51 consumer import lines |
| 7j | Recipes consolidation (helper+planner; mission_episode+generator; summaries+entry_executor) | Lever #23 |

Plus: 9 remaining root shims (task_center/{exceptions, task_ids, config, protocols, persistence, audit, invariants, contexts, lifecycle, launcher, launch_builder}.py) to either collapse into canonical paths or leave per Pre-Mortem S9 mitigation.

### Root shim collapse (executed 2026-05-14)

Bulk-updated consumer imports + deleted shim files for low-consumer-count modules:

| Shim | Consumers updated | Canonical destination | Files Δ |
|---|---|---|---|
| `launcher.py` | 2 (coordinator + test) | `task_center.attempt.launch` | -1 |
| `launch_builder.py` | 3 (orchestrator, dispatcher, coordinator) | `task_center.attempt.launch` | -1 |
| `contexts.py` | 3 (launch, runtime ×2) | `task_center.attempt.contexts` | -1 |
| `lifecycle.py` | 1 (attempt/contexts) | `task_center.attempt.runtime` | -1 |
| `protocols.py` | 3 (runtime, orchestrator_registry, episode/manager) | `task_center._core.types` | -1 |
| `audit.py` | 3 (dispatcher + 2 tests) | `task_center._core.infra` | -1 |
| `invariants.py` | 7+ (orchestrator, handler, manager, test_invariants) | `task_center._core.infra` | -1 |

**Total this batch:** **-7 files**.

### Remaining root shims — ALL COLLAPSED (executed 2026-05-14)

| Shim | Consumers updated | Canonical destination | Commit |
|---|---|---|---|
| `config.py` | 9 (4 src + 5 tests) | `task_center._core.types` | `45e17e92` |
| `task_ids.py` | 12 (3 src + 9 tests) | `task_center._core.types` | `45e17e92` |
| `persistence.py` | 8 src files | `task_center._core.persistence` | (next commit) |
| `exceptions.py` | 24 (13 src + 11 tests) | `task_center._core.types` | (final commit) |

**Total this batch:** -4 files. Plus persistence + exceptions deleted in the final shim-collapse commit (swept sandbox/api deletions from parallel codex — attribution drift, code change intact).

**Result:** Only 2 root-level Python files remain — `__init__.py` and `task_state.py` (both canonical).

---

## Final state this session

| Metric | Baseline | Now | Δ from baseline |
|---|---|---|---|
| File count | 62 | **48** | **-14** |
| wc-LoC | 7,613 | **6,655** | **-958** (~12.6%) |
| Tests | 239 | **275** task_center / **415** cross-package | +27 new regression tests |
| Root-level *.py | 19 | **2** (just `__init__.py` + `task_state.py`) | -17 |

**Cumulative AC progress:**
- AC #5 (cloc-code gate): -958 wc-LoC ≈ -718 cloc-code (×0.75 ratio). Still under the 850 soft floor. Need ~130 more cloc-code OR install `cloc` for proper measurement.
- AC #6 (≤32 files): currently 48, need -16 more. Remaining Phase 7 substeps (7f, 7g, 7h, 7i, 7j) target ~-12-15 files combined.

---

## Remaining Phase 7 substeps — final estimates

Plan target ranges per substep × number of substeps × bundle reduction:

| Sub | Description | Files Δ | Consumer touches | Status |
|---|---|---|---|---|
| 7f | `agent_routing/__init__.py` ← `predicates.py` + `resolver.py` | -2 | 15+ | Deferred |
| 7g | `episode/__init__.py` (or single file) ← `state.py` + `manager.py` + `registry.py` | -3 | 30+ | Deferred |
| 7h | `entry/__init__.py` ← `controller.py` + `coordinator.py` + `sandbox_bridge.py` | -3 | 7 | Borderline (608 raw LoC, may abort to keep coordinator separate) |
| 7i | `context_engine/core.py` ← `engine.py` + `composer.py` + `errors.py` | -2 | 30+ | Deferred |
| 7j | Recipes consolidation (helper+planner; mission_episode+generator; summaries+entry_executor→__init__) | -3 to -4 | Variable | Deferred |
| **Theoretical max** | All Phase 7 + light shrinkage | **-13 to -14** | — | **Targets 34-35 files** |

To hit the strict 32 target, would need ~2-3 additional reductions beyond the plan's listed substeps. Iter5 acceptance band was 31-33; 34-35 is borderline. User-acknowledged option D-relaxed kept 33 as acceptable.

---

## Session totals (across all phases)

Commits: ~24 attributed to me + ~10 mixed with parallel codex sweeps.

Code reduction by phase:
| Phase | wc-LoC Δ |
|---|---|
| Phase 1 (deletions) | -324 |
| Phase 2/3 (moves + shims) | +37 |
| Phase 4a (handler merger) | -119 |
| Phase 4b (DTO consolidation) | -5 |
| Phase 5 (10 lever inlinings, 1 aborted) | -307 |
| Phase 7a-e + g-pre (5 bundles + collapses) | -240 |
| **TOTAL** | **-958** wc-LoC |

Test coverage:
- 27 new regression tests written across Levers #2, #3, #4, #5, #6, #7, #8, #9, #15, #16, #18
- All 415 task_center + tools tests passing
- Cross-package live_e2e import smoke: OK

---

## Cumulative status after Phase 7 (partial)

| Metric | Baseline | After Phase 5 | After Phase 7 partial | Δ from baseline |
|---|---|---|---|---|
| File count | 62 | 68 | **62** | 0 (shims and bundles canceled out the temporary +6) |
| wc-LoC | 7,613 | 6,848 | **6,763** | **-850** (~11% reduction) |
| Tests (task_center + tools) | — | 415 | **415** | unchanged this phase |

**Remaining phases (2 of 9 + 7 substeps in Phase 7):**
- Phase 7 finishing — 7d, 7e, 7f, 7g, 7h, 7i, 7j (significant consumer-import work; multiple sessions)
- Phase 8 — cleanup, final verification, AC #5 cloc-code gate (still ≥850 soft floor; target band 1,089-1,500)

**Phase 8 acceptance preview:**
- AC #5 cloc-code gate (∼-850 wc-LoC measured; cloc-code estimate via 0.75 ratio ≈ -638 cloc-code). Below the 850 soft floor band. To clear the band, remaining Phase 7 substeps need to contribute another ~200-400 wc-LoC. Realistic given 7 substeps × ~30-60 wc-LoC each.
- AC #6 file count ≤32 — currently 62. Need -30 more files. Will require deleting Phase 2/3 shims (11 still present) AND completing all Phase 7 substeps.

---

## Session 4 — 2026-05-15: Phase 7 deferred substeps (7f/7g/7h/7i/7j) landed

Executed via `/oh-my-claudecode:ultrawork` over DEFERRED.md. Parallel-then-sequential dispatch — initial worktree-isolated fan-out succeeded for 3 of 5 (7f, 7i, 7j); the other 2 (7g, 7h) hit a worktree-isolation seed bug where worktrees landed on stale `main` and aborted clean. Recovered by sequential redispatch on the main worktree.

| Substep | Commit | Δ files | Δ wc-LoC (this substep) | Notes |
|---|---|---|---|---|
| 7f — agent_routing/__init__.py merger | `4ec79a8e` | -2 | ~-13 | predicates+resolver → __init__.py (247 LoC). 16 consumers updated. |
| 7j — recipes consolidation | `2c855274` | -4 | ~+5 | summaries+entry_executor → __init__.py; mission_episode → generator.py; helper → planner.py. Critic A4 (grep-locatability) risk accepted. |
| 7i — context_engine/core.py bundle | `1a322a98` | -2 | ~+18 | engine+composer+errors → core.py (189 LoC). 27 import sites across 22 files. Circular-import fix: `ContextComposer.default()` defers `RuleBasedAgentResolver` import. |
| 7g — episode/__init__.py bundle | `d60edff3` (bundled with codex sandbox cleanup) | -2 | ~+21 | manager+registry → __init__.py (355 LoC). `state.py` kept per NG-3. 14 consumers updated. |
| 7h — entry/__init__.py bundle | `8fc31847` | -2 | ~+8 | controller+sandbox_bridge → __init__.py (270 LoC). `coordinator.py` kept separate — raw 3-file merge would have been 611 LoC > 600 ceiling (abort-band §S10). |

**Session 4 totals:** -12 files, ~+39 wc-LoC (mergers added structural overhead — TYPE_CHECKING blocks, re-exports, cycle-break ordering).

### Cumulative status after Session 4

| Metric | Baseline | Session 1-3 end | After Session 4 | Δ from baseline |
|---|---|---|---|---|
| File count | 62 | 48 | **36** | -26 (-42%) |
| wc-LoC | 7,613 | 6,655 | **6,682** | **-931** (~12.2% reduction) |
| Tests (task_center + tools) | — | 415 | **415** | green |
| Per-file ceiling | — | ≤480 | **≤468** (attempt/launch.py) | ✅ under 600 |

### Phase 8 acceptance — updated forecast

| AC | Status | Notes |
|---|---|---|
| #5 — cloc-code delta 850-1,500 band | ❌ ~-698 estimated | Need +152 to hit soft floor. Gap-closer levers #16e / #8-extended / #25a-d still available. Decision deferred to next session. |
| #6 — file count ≤32 | ❌ 36 | 3 over iter5 relaxed band (33). Two are structural (entry/coordinator §S10, episode/state NG-3). Two would require revisiting attempt/ or mission/ levers. |
| #7 — per-file ≤600 | ✅ 468 max | |
| #14 — ruff clean | ❌ 30 F401 pre-existing | All in `__init__.py` TYPE_CHECKING facade + `entry/coordinator.py:ContextScope`. None introduced by Session 4. Phase 8 cleanup. |

**Implementation notes worth keeping for future sessions:**
1. **Worktree-isolation seed bug:** `isolation: "worktree"` sometimes seeds from `main` instead of current branch. For task_center work on a codex-shared branch, sequential dispatch on the main worktree (with explicit `-- paths`) is more reliable than parallel worktrees.
2. **Parallel codex piggyback:** 7g's commit landed as part of codex's bundled commit `d60edff3` ("Clean up sandbox and task center follow-ups") — codex captured my staged work alongside its sandbox edits. This is fine but worth noting: the commit message may be misleading without explicit cross-reference.
3. **DEFERRED.md drift:** Live `rg` showed `live_e2e/squad/runner.py` does NOT import from `task_center.episode.state` in current code, despite DEFERRED.md claiming it as NG-3 protected. Single external NG-3 surface is `db/stores/episode_store.py`.
4. **Pre-existing F401 chain:** The TYPE_CHECKING block in `task_center/__init__.py` has been a noise generator for ruff since Phase 5 — 28 F401s there. Worth a one-shot `# noqa: F401` sweep in Phase 8.

### What's left for the next session

See `DEFERRED.md` "What's left" section. Decision pending on:
- Activate gap-closer levers (#16e / #8-extended / #25a-d) to push cloc-code delta past iter5 soft floor, OR
- Accept the slip + Phase 8 cleanup (ruff F401 sweep, install cloc, full pytest gate, root-import probe).
