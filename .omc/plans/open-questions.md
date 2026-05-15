# Open Questions

## task-center-folder-reframe-20260514 — 2026-05-14

- [ ] **Where should `task_center/protocols.py` live after reframe — root or `_core/`?** — It contains `RegisteredAttemptOrchestrator` and `RegisteredEpisodeManager` (a collaboration seam between attempt/episode subpackages). Default in plan = pin at root (no move). Decide during execution Phase 3 based on a `grep -rn "from task_center.protocols\|task_center.protocols" backend/` outside `task_center/` — if zero external callers, move to `_core/protocols.py` for tighter folder cohesion. If any external caller exists, pin and document.
- [ ] **Should `EpisodeClosureReport` and `CloseReportDeliveryResult` be added to `_EXPORTS`?** — Currently only reachable via deep submodule import. Adding them widens the public surface for symmetry with `Episode`/`Mission` peers. Plan keeps them deep-only to satisfy "no new abstractions / no public-surface widening." Revisit if downstream callers begin importing them through the facade.
- [ ] **Should the 3 root re-export shims (`task_ids.py`, `exceptions.py`, `audit.py`) be retired in a follow-up cycle?** — Plan keeps them indefinitely to avoid touching external packages. After 1–2 release cycles, audit whether all callers have migrated to deep `_core/` paths, then delete the shims for ~9 more LoC.
- [ ] **Does `mission/` warrant a deeper SRP audit?** — Plan's Principle 4 said "do not collapse behavior siblings," but `episode_factory.py` / `episode_closure_router.py` / `close_report_router.py` / `handler.py` / `repository.py` may have overlapping responsibilities. A separate plan, after this reframe lands, could collapse the 8-file subpackage to 4–5. Out of scope here because it crosses into behavioral analysis.
- [ ] **Will parallel user commits during execution cause merge conflicts?** — Per `feedback_parallel_user_commits.md` memory, the user runs codex in parallel. Execution agent must (a) stage with explicit file paths only, (b) verify HEAD between phases, (c) never `git add task_center/`. Confirm with user before kicking off long-running phases.

## task-center-folder-reframe-20260514 — iter4 additions — 2026-05-14

- [ ] **iter4: ≤32 files vs 33 files with re-export shells** — Iter4 plans for 31 files via merging `recipes/mission_episode.py`→`generator.py` and `recipes/helper.py`→`planner.py`. If user rejects those recipe merges, fall back to 33 files which violates ≤32 ceiling by 1. Alternative: edit `live_e2e/squad/runner.py:33-34` imports to use root facade, overriding NG-3. Decide before Phase 7.
- [ ] **iter4: cloc-code floor (1,089) risk at Phase 8** — Conservative roll-up of base levers + secondary mergers nets ~815–1,257 cloc-code; midband ≈ 1,036, below floor. Phase 8 gap-closer activates deferred backlog (lever #16d/e StageStrategy + full Ctx Protocol→AttemptDeps + lever #8-extended invariants). Surface deficit to user before declaring done if still under after deferred backlog.
- [ ] **iter4: Mission Merger Contract relaxation (iter2 ≤300 → iter4 ≤480)** — Phase 4a retains ≤300 ceiling. Phase 7c absorbs `repository.py`+`ancestry.py` and relaxes ceiling to ≤480. Iter2 regression test `test_mission_handler_merged_dependencies_isolated.py` must be amended to assert ≤480 post-Phase-7c. Confirm relaxation is acceptable; otherwise abort Phase 7c absorb (file-count climbs to 33).
- [ ] **iter4: `entry/__init__.py` bundle at 547 LoC tight to 600 ceiling** — Buffer 53 LoC. If post-merge measurement >600, abort Phase 7h entry bundle (file-count climbs to 32 or 33 depending on shells decision). Confirm fallback plan.
- [ ] **iter4: cloc binary may be absent** — Phase 8 cloc verification requires `cloc`. Phase 0 gate: `which cloc || brew install cloc`. Confirm executor has Homebrew install permission OR pre-install cloc before Phase 0.

## task_center_runner-restructure — 2026-05-15 (RESOLVED — handoff)

All 5 questions answered by user 2026-05-15. Final plan: `.omc/plans/task_center_runner-restructure.md` (§10 Handoff Brief).

- [x] **Perf-report schema string** → bump to `task_center_runner.performance_report.v2` in Phase 3 (with `grep -rn live_e2e.performance_report.v1` consumer audit before merge).
- [x] **`RunConfig.run_dir_factory` default** → unify on `audit_dir/<run_label>/<utc>_<self_id>` for all modes; Phase 4 updates `run_tiered.py` resume path-mapper.
- [x] **`SandboxProvisioner.release` semantics** → default destroys (best-effort); `AttachExisting` overrides to no-op for pre-provisioned test fixtures.
- [x] **`live_e2e/` shim DeprecationWarning** → drop entirely; shim is silent (shim removal is the migration trigger).
- [x] **LLM provider key** → active model `minimax` (MiniMax-M2.7) per `<repo>/models/registry.json`; env vars `MINIMAX_API_KEY` + `MINIMAX_BASE_URL`. Wiring: `db.stores.model_store.ModelStore.get_active()`; seeded by `runtime.app_factory.ensure_runtime_stores_ready()` (called by `bootstrap_real_agent_runtime()`). Plan does not change this.
