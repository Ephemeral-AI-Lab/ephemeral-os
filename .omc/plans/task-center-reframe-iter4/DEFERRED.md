# DEFERRED — task_center reframe iter4 remaining work

**Updated:** 2026-05-15
**Plan:** `../task-center-folder-reframe-20260514.md`
**Manifest:** `./PHASE-0-MANIFEST.md`

**Session 2026-05-15 update:** All 5 deferred Phase 7 substeps (7f, 7g, 7h, 7i, 7j) landed in a parallel-then-sequential ultrawork run. File count target hit within iter5 relaxed band overshoot tolerance; wc-LoC slightly regressed due to re-export/TYPE_CHECKING overhead. Phase 8 verification + cleanup remains.

---

## Current state (post 7f/7g/7h/7i/7j)

| Metric | Value | Target | Verdict |
|---|---|---|---|
| File count | **36** | ≤32 (iter5 relaxed: 31–33) | ⚠️ 3 over relaxed band |
| wc-LoC | **6,682** | ≥cloc-code -1,089 from baseline 5,443 | ⚠️ regressed +27 from session start |
| wc-LoC delta from baseline (7,613) | **-931** (~12.2%) | — | down from session-start -958 |
| cloc-code estimate (×0.75) | ~-698 | iter5 soft floor: -850 | ❌ ~152 under soft floor |
| Tests | 415 / 275 task_center | All green | ✅ 415 passing post-7h |
| Root-level `*.py` | 2 (`__init__.py`, `task_state.py`) | 2 | ✅ |
| Root shims remaining | 0 | 0 | ✅ |
| Per-file ceiling | 468 max (attempt/launch.py) | ≤ 600 | ✅ |

**Files by folder (target vs actual, post-substeps):**

| Folder | Now | Plan target | Δ remaining | Notes |
|---|---|---|---|---|
| `_core/` | 4 | 4 | ✅ at target | |
| `agent_routing/` | 1 | 1 | ✅ at target | 7f landed |
| `attempt/` | 9 | 9 | ✅ at target | |
| `context_engine/` | 6 | 6 | ✅ at target | 7i landed (engine+composer+errors → core.py) |
| `context_engine/recipes/` | 5 | 6 | +1 better | 7j landed (-4 files via 3-way fold) |
| `entry/` | 2 | 1 | -1 | 7h landed (abort-band: kept coordinator.py separate) |
| `episode/` | 2 | 1 | -1 | 7g landed (state.py kept per NG-3) |
| `mission/` | 5 | 5 | ✅ at target | |
| root | 2 | 2 | ✅ at target | |
| **Total** | **36** | **30** | **6 over plan target** | iter5 relaxed band 31-33: 3 over |

**The 6-file gap from the original ≤32 plan target breaks down as:**
- entry/coordinator.py kept separate (+1) — abort-band §S10 — raw 3-file merge was 611 LoC > 600 ceiling
- episode/state.py kept separate (+1) — NG-3 external import from db/stores/episode_store.py
- The other 4 are inside attempt/, mission/, _core/ — already at plan-stated targets but those targets themselves total 18, not the original "rough" 14 cited in some sections of the plan. No simple win available without further levers.

---

## Phase 7 substeps — final landing log

### 7f — agent_routing/__init__.py merger ✅ LANDED

**Commit:** `4ec79a8e` (2026-05-15)
**Δ:** -2 files (predicates.py, resolver.py merged into __init__.py at 247 LoC)
**Consumers updated:** 16 files
**Tests:** 415 passed

### 7g — episode/__init__.py bundle ✅ LANDED

**Commit:** `d60edff3` (2026-05-15, bundled with parallel codex sandbox cleanup)
**Δ:** -2 files (manager.py + registry.py merged into __init__.py at 355 LoC; state.py kept per NG-3)
**Consumers updated:** 14 files
**NG-3 verified:** `db/stores/episode_store.py` import path still resolves; live_e2e/squad/runner.py does NOT actually import from task_center.episode.state in current code (DEFERRED.md was stale on that point)
**Tests:** 415 passed

### 7h — entry/__init__.py bundle (abort-band variant) ✅ LANDED

**Commit:** `8fc31847` (2026-05-15)
**Δ:** -2 files (controller.py + sandbox_bridge.py merged into __init__.py at 270 LoC; coordinator.py kept separate per §S10)
**Why abort-band fired:** Raw 3-file merge would have been 611 LoC > 600 ceiling. Plan §S10 mitigation invoked.
**Consumers updated:** 7 import sites across 6 files
**Tests:** 415 passed

### 7i — context_engine/core.py bundle ✅ LANDED

**Commit:** `1a322a98` (2026-05-15)
**Δ:** -2 files (engine.py + composer.py + errors.py consolidated into core.py at 189 LoC)
**Consumers updated:** 27 import sites across 22 files
**Implementation note (worth keeping):** `ContextComposer.default()` defers `from task_center.agent_routing import RuleBasedAgentResolver` to break a circular import. Exception classes are defined BEFORE the downstream imports of packet/recipes_registry/renderer/scope (those back-import error names from core).
**Tests:** 415 passed

### 7j — Recipes consolidation ✅ LANDED

**Commit:** `2c855274` (2026-05-15)
**Δ:** -4 files (summaries.py + entry_executor.py → __init__.py; mission_episode.py → generator.py; helper.py → planner.py)
**Risk noted from Critic A4 (grep-locatability):** Accepted in plan Option A.
**Tests:** 275 task_center passed

---

## Deferred levers from original plan (still NOT activated)

These are gap-closers for AC #5 (cloc-code delta) and could be activated to close the ~152-LoC gap under the iter5 soft floor.

### Lever #16e (Ctx Protocols → AttemptDeps consolidation)

**Estimate:** +80-140 cloc-code if activated.
**Status:** Deferred per plan iter4 conservative scope. Trigger: Phase 8 AC #5 shortfall — that trigger has now FIRED.

### Lever #8-extended (additional invariants inlining)

**Estimate:** +30-60 cloc-code from inlining 5-8 additional assertion call sites.
**Trigger:** Phase 8 cloc-code gate failure — FIRED.

### Lever #25c (dispatcher.py parameterization)

**Estimate:** +30-60 cloc-code. `_dispatch_generating` (line 93) + `_dispatch_evaluating` (line 124) parameterization.

### Lever #25a/b/d (small duplicates)

**Cumulative estimate:** 30-60 cloc-code.
- `_fresh_attempt` duplication in orchestrator + dispatcher (#25a)
- `_assert_stores_ready` duplication in coordinator (#25b)
- Single-use module-level helpers `_parent_attempt_id`, `_task_agent_name` (#25d)

### Lever #26 (post-merger boilerplate sweep)

**Estimate:** +20-40 cloc-code. Cross-file boilerplate sweep after attempt/launch.py merger. **Partially executed in Phase 7d.**

### Lever #16d (StageStrategy Protocol)

**Status:** Deferred — Lever #7 covered the same module differently. No further action unless gap-closing requires it.

---

## Phase 8 — Cleanup + Final Verification (PARTIAL — see below)

| Check | Status |
|---|---|
| 1. Trim `__init__.py` docstring + confirm `_EXPORTS` | ⚠️ Not yet run; 29-30 F401 ruff errors in TYPE_CHECKING block per 7h agent |
| 2. Ruff clean | ❌ 30 pre-existing F401 errors (28 in `task_center/__init__.py` TYPE_CHECKING facade + ContextScope in `entry/coordinator.py`) — NONE introduced by 7f/7g/7h/7i/7j |
| 3. Root-import probe (`python -c "import task_center; ..."`) | ⚠️ Not yet run |
| 4. Full pytest (`backend/tests/ -x`) | ⚠️ Targeted suite (415 tests in test_task_center + test_tools) green; full backend tests NOT yet run |
| 5. cloc verification | ❌ `cloc` not installed; install via `brew install cloc` to claim AC #5 exact. wc-LoC estimate via ×0.75: ~-698 cloc-code, ~152 under iter5 soft floor |
| 6. File count ≤32 | ❌ 36 — 3 over iter5 relaxed band, 4 over original target |
| 7. Per-file ≤600 | ✅ Max 468 (`attempt/launch.py`) |

**Gap-closer logic (plan §S8):**
- cloc-code delta < 850 → activate deferred backlog (#16e, #8-extended, #25a/b/c/d, #26-residual) and re-verify. **Currently FIRED. Decision needed: activate gap-closers, accept the slip, or revise targets.**

---

## Acceptance Criteria — final snapshot

| AC | Description | Status | Gap |
|---|---|---|---|
| 1 | All tests + cross-package callers pass | ✅ 415 passing | — |
| 2 | External callers compile without edits (NG-3) | ⚠️ Verify pre-release | git diff backend/src/{db,tools,live_e2e,agents} should be empty for non-test paths; verified empty per 7g agent |
| 3 | `task_center/events.py` removed | ✅ | — |
| 4 | Root-import probe prints lazy | ⚠️ Not re-verified post-substeps | Run in Phase 8 |
| 5 | cloc-code delta in 850-1,500 band | ❌ ~-698 estimated | Need +152 cloc-code OR install cloc for exact; activate gap-closer levers OR formally accept slip |
| 6 | File count ≤ 32 | ❌ 36 | -4 needed; or formally accept iter5 relaxed band miss by 3 |
| 7 | Every file ≤ 600 LoC | ✅ 468 max | — |
| 8 | `_EXPORTS` keyset unchanged | ⚠️ Verify | Diff against baseline |
| 9 | Zero external public-signature changes | ⚠️ Verify | inspect.signature snapshots |
| 10 | `mission/handler.py` ≤480 LoC | ✅ 421 | — |
| 11 | Other bundle ceilings | ✅ all ≤468 | — |
| 12 | All per-lever regression tests pass | ✅ | — |
| 13 | `live_e2e/squad/runner.py` + `real_agent_run.py` import smoke | ⚠️ Re-verify post-7g | NG-3 surface still resolves |
| 14 | `ruff check` clean | ❌ 30 pre-existing F401 | Phase 8 cleanup work |

---

## What's left for the next session

1. **Decide on AC #5/#6 slip vs gap-closer activation.** Options:
   - **Accept the slip and ship:** Update plan to record 36-file / ~-698-cloc final state. File-count win is still -12 (-25%); LoC win is -931 wc-LoC overall from baseline.
   - **Activate gap-closer levers:** Run #16e + #8-extended + #25a/b/d in a follow-up session. Estimated total +180-260 cloc-code → would land at -880 to -960 cloc-code (in band).
2. **Phase 8 ruff cleanup.** 30 pre-existing F401 errors in `task_center/__init__.py` TYPE_CHECKING block + `entry/coordinator.py`. Fix pattern: add `# noqa: F401` or restructure the TYPE_CHECKING facade.
3. **Full pytest gate.** `.venv/bin/pytest backend/tests/ -x` (not just test_task_center + test_tools).
4. **Install cloc.** `brew install cloc`. Re-run AC #5 verification with exact cloc-code numbers.
5. **Root-import probe.** Re-verify lazy import contract.
6. **Update PHASE-0-MANIFEST.md** with the 5 landing commits (deferred to manifest-update task).

## Resume checklist (per session)

1. Read this DEFERRED.md
2. Read `PHASE-0-MANIFEST.md` for full history
3. Read `../task-center-folder-reframe-20260514.md` for plan reference
4. Pick: Phase 8 verification, gap-closer levers, or accept-slip-and-ship
5. Standard flow:
   - Read source files
   - Plan changes + identify consumers (`rg`)
   - Update consumers in batches (Edit with replace_all)
   - Run `.venv/bin/pytest backend/tests/unit_test/test_task_center/ backend/tests/unit_test/test_tools/ -q`
   - Commit with explicit `-o <paths>` to bypass parallel-codex staging sweep
6. Update this DEFERRED.md + PHASE-0-MANIFEST.md after each substep

## Project memory cautions (DO NOT IGNORE)

- **Parallel codex commits:** User runs codex in parallel on this same branch. Stage with explicit file paths only; `git commit -am` will sweep their staged changes too. Verify HEAD before declaring done. **Confirmed multiple times this session: codex committed `d60edff3`, `12ec5bd7`, and others alongside our 7f/7g/7h/7i/7j work.**
- **`.venv/bin/pytest`:** Global pytest reports ~88 spurious failures; always use `.venv/bin/pytest`. Same goes for ruff.
- **`invariant_replan_dependents_must_be_pending`:** Preserve `assert_replan_dependents_must_be_pending` function name (currently not present; future invariants must keep this naming).
- **`cloc` not installed:** All cloc-code numbers in this work are wc-LoC × ~0.75 approximations. Install `brew install cloc` before claiming AC #5 victory.
- **Worktree isolation gotcha:** `isolation: "worktree"` in this harness sometimes seeds worktrees from `main` instead of the current branch. Two of five parallel substep agents got stale-main worktrees and aborted. For task_center work on a codex-shared branch, sequential dispatch on the main worktree (with explicit `-- paths` staging) is more reliable than parallel worktrees.
