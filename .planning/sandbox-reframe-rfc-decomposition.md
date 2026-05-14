# Sandbox Reframe — Ralphinho RFC Pipeline Decomposition

**Source RFC:** `.planning/sandbox-reframe-plan.md`
**Pipeline:** ralphinho-rfc-pipeline (decompose → DAG → assign → implement → validate → merge queue → final verify)
**Stage status:** Stages 1–3 complete in this document. Stages 4–7 deferred to execution.

---

## 1. RFC Intake Summary

- **Target tree:** `backend/src/sandbox/` — 160 .py files, ~17,492 LOC, 10 top-level subdirs.
- **Hard constraint:** **no behavior change.** Renames/codemods only; signatures and public symbols of `sandbox.api` rename-frozen.
- **Numeric targets:**
  - ≥1,222 LOC deletion (firm floor); stretch ~1,400 with vulture.
  - 160 → ≤152 files; 10 → 9 top-level subdirs.
  - 0 files >600 LOC; 0 files >500 LOC without justification.
- **Codemod surface:** 209 ImportFrom rewrites (107 internal + ~102 test).
- **Risk anchors:** Wave 3 (runtime→daemon rename + tar bundle), Wave 5b pre-flight gate, Wave 7c (Daytona dedup, 300s-hang failure mode per memory `daytona_pending_build_root_cause.md`).

---

## 2. DAG Snapshot (work-unit dependency graph)

```
PREP-0 (codemod script) ─┐
PREP-0b (bench baseline) ┤
                         ├─→ W0 (junk) ──┐
                         │               ├─→ W1 (api shim merge) ──┐
                         │               │                         ├─→ W1.5 (flatten) ─→ W2 (exec merge) ─→ W3 (daemon rename) ─→ W4a (vulture) ─→ W4b (narration)
                         │                                                                                                                              │
                         └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────│
─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
                                                                                                                                                         ▼
                                                                                                                                                    W5a (drop sync)
                                                                                                                                                         │
                                                                                                                                                         ▼
                                                                                                                                                  PREP-5b (pre-flight)
                                                                                                                                                         │
                                                                                                                                                         ▼
                                                                                                                                                W5b (daemon/service inline)
                                                                                                                                                         │
                                                                                                                                                         ▼
                                                                                                                                                  W5c (contract collapse)
                                                                                                                                                         │
                                                                                                                                                         ▼
                                                                                                                                                  W6 (Protocol thin)
                                                                                                                                                         │
                                                                                                                                                         ▼
                                                                                                              W7a (api/_impl) → W7b (handler trio) → W7c (Daytona dedup)
                                                                                                                                                         │
                                                                                                                                                         ▼
                                                                                                                              W8a (api inlines) ─┬─→ W8b
                                                                                                                                                 ├─→ W8c
                                                                                                                                                 └─→ W8d
                                                                                                                                                         │
                                                                                                                                                         ▼
                                                                                                                                                       W9 (occ/stage)
```

**Parallelism opportunities (within otherwise-linear plan):**
- `W8b`, `W8c`, `W8d` are mutually independent after `W8a` lands. Can fan out to 3 agents.
- `W4a` (vulture) and `W4b` (narration) touch different files; RFC orders 4a→4b for clean blame but they could parallelize on separate branches.
- All Round-2 waves are gated by `W4b` because Round-2 audits assume the renamed layout.

---

## 3. Complexity Tiers

| Tier | Definition | Units |
|---|---|---|
| **T1 — isolated edits, deterministic tests** | Single-concern, no codemod, no daemon-bundle / public-surface risk | PREP-0, PREP-0b, W0, W1, W4a, W4b, W5a, PREP-5b, W5c, W6, W7b, W8b, W8c, W8d |
| **T2 — multi-file behavior changes, moderate integration risk** | Codemod across many imports; consolidation that touches mock seams | W1.5, W2, W5b, W7a, W8a, W9 |
| **T3 — schema/auth/perf/security/critical-runtime changes** | Daemon-bundle path, network/provider lifecycle, irreversible perf characteristics | W3 (tar bundle + `-m` path), W7c (Daytona client cache isolation) |

---

## 4. Unit Scorecards

> Each unit follows the skill's required schema: `id`, `depends_on`, `scope`, `acceptance_tests`, `risk_level`, `rollback_plan`.

### PREP-0 — Codemod script (libcst, ImportFrom-only)
- **depends_on:** —
- **scope:** Commit `backend/scripts/codemod_sandbox_imports.py` (libcst-based; ImportFrom + Import nodes only; rewrite map via argv JSON). No source-tree changes.
- **acceptance_tests:** `python backend/scripts/codemod_sandbox_imports.py --dry-run --map='{}' backend/` exits 0; unit test that the visitor leaves `cst.SimpleString` / `cst.Name` untouched (synthetic fixture).
- **risk_level:** T1.
- **rollback_plan:** `git revert` — pure tooling.

### PREP-0b — Bench baseline + Wave-5b pre-flight script scaffolding
- **depends_on:** —
- **scope:** Commit `backend/scripts/bench_sandbox_e2e.py` (driver around `MockSquadRunner`, emits `svc_cmd_p50/p95` JSON); run once to capture `baseline.json`; commit pre-flight script `backend/scripts/check_wave5b_preflight.sh` per §14 enforcement chain.
- **acceptance_tests:** `bench_sandbox_e2e.py --commands 10 --report=baseline.json` emits JSON with `svc_cmd_p50` key; `EOS_TIER_RUN_ID` env honored per memory `eos_tier_run_id_artifact_stability.md`.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W0 — Junk + dead skeleton purge
- **depends_on:** —
- **scope:** Delete 7 empty skeleton dirs, 10 `.DS_Store`, `layer_stack/IMPLEMENTATION_REPORT.md`, stale `__pycache__/*.pyc` for renamed modules; drop empty `__init__.py` markers per §4.Wave 0.
- **acceptance_tests:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q`; `python -c "import sandbox.api"`.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W1 — Verified api shim merge
- **depends_on:** PREP-0, W0.
- **scope:** Inline `api/defaults.py` (15 LOC) into `api/default.py`; update callers.
- **acceptance_tests:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q`; `python -c "import sandbox.api.default"`.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W1.5 — Flatten 4 single-file `layer_stack/` subdirs
- **depends_on:** PREP-0, W1.
- **scope:** `commit/`, `lease/`, `maintenance/`, `view/` (each 1 file + `__init__.py`) → top-level files. Rewrite **5 verified test imports**. (`command_exec/entrypoints/` flatten folded into W2.)
- **acceptance_tests:** Grep gate `grep -rE "sandbox\.layer_stack\.(commit|lease|maintenance|view)\."` returns 0 hits outside `/sandbox/`; `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_layer_stack -q`; ruff clean.
- **risk_level:** T2 (test rewrites required).
- **rollback_plan:** `git revert`; codemod is reversible by inverting rewrite map.

### W2 — `command_exec/` + `overlay/` → `execution/` (+ overlay-trio merge)
- **depends_on:** PREP-0, W1.5.
- **scope:**
  - Move `command_exec/{executor,policy,contract/,strategies/,workspace/}` → `execution/{orchestrator,policy,contract/,strategies/,workspace/}`.
  - Flatten `command_exec/entrypoints/namespace_helper.py` → `execution/entrypoints.py`; `-m` target becomes `sandbox.execution.entrypoints`.
  - Merge `overlay/{factory,invoker,command}` (282 LOC) → `execution/overlay/pipeline.py` (≤200 LOC after dedup).
  - Codemod 209 ImportFrom sites; delete `command_exec/__init__.py` lazy-export dict; delete `overlay/cli.py` shim.
  - String-literal edits (verified line nos): `command_exec/strategies/private_namespace.py:93`; `command_exec/__init__.py:24-37`.
  - **Preserve audit event-name string `"sandbox.overlay.executed"`** in `audit/events.py`.
- **acceptance_tests:** Grep gates per §8.3 (`sandbox.command_exec` = 0, `sandbox.overlay\b` = 0 except inside `sandbox.execution.overlay` and `audit/events.py`, `overlay.factory|invoker|command` = 0); audit-contract preserved (`grep '"sandbox.overlay.executed"' backend/src/sandbox/audit/events.py` returns 1); unit + integration tests green; live_e2e smoke + roundtrip green; bench svc.cmd p50 within 5%.
- **risk_level:** T2.
- **rollback_plan:** `git revert` (single commit). Bundle-hash cache invalidates once but no production-breaking effect at this wave.

### W3 — `runtime/` → `daemon/` rename (HIGHEST RISK)
- **depends_on:** W2.
- **scope:**
  - Move `runtime/` → `daemon/` (top-level); subdirs preserved verbatim.
  - Codemod (libcst ImportFrom-only) for `sandbox.runtime.daemon|scripts|async_bridge` → `sandbox.daemon.*` (68 test refs).
  - Manual surgical string edits at **5 behavior-critical line numbers** (per §4.Wave 3):
    - `daemon_paths.py:17` `RUNTIME_SCRIPT_DIR` value path
    - `host/runtime_bundle.py:113,182` (tar source-path + arcname)
    - `host/daemon_client.py:331` (`-m sandbox.daemon`)
    - `runtime/daemon/__main__.py:18` (`prog=`)
  - 8 cosmetic edits (docstrings, log names, comments) listed in §4.Wave 3.
  - Keep identifiers `runtime_bundle_bytes`, `ensure_runtime_uploaded`, `RuntimeBundle`, `RUNTIME_SCRIPT_DIR` constant name (payload concept).
  - Keep `runtime.sock|pid|log|env` filename strings (in-sandbox files).
- **acceptance_tests:** 5 path-shaped grep gates per §8.3 Wave-3 (all return 0); unit + integration green; **manual live_e2e against real provider with `provider.create()` 60s timeout** (mitigates Scenario A daemon-boot hang per memory `daytona_pending_build_root_cause.md`); bench p50 within 5%; bundle-hash invalidation documented in commit message.
- **risk_level:** T3.
- **rollback_plan:**
  - Pre-merge: `git revert` HEAD; codemod has dry-run JSON of all rewrites for reverse application.
  - Post-merge if daemon-boot fails in prod: **fast-revert the wave**; bundle-hash recomputes back to old path; on-call playbook = restart sandboxes (one-time re-upload).
  - Observability: log channel `sandbox.runtime.daemon.*` → `sandbox.daemon.*`. Documented in commit message; ops alerted.

### W4a — Vulture / dead-symbol audit
- **depends_on:** W3.
- **scope:** `vulture backend/src/sandbox --min-confidence 80 backend/scripts/vulture_whitelist.py` → delete confirmed-dead in single commit. Whitelist `RUNTIME_SCRIPT_DIR` and similar intentional constants.
- **acceptance_tests:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q`; `.venv/bin/ruff check backend/src/sandbox` clean.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W4b — Narration-comment compression
- **depends_on:** W4a.
- **scope:** `host/bootstrap.py` ~17 lines, `provider/daytona/adapter.py` ~15 lines, `host/daemon_client.py` ~12 lines; confirm `__pycache__` in `.gitignore`.
- **acceptance_tests:** unit tests green; final LOC report (`find ... | xargs wc -l`).
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W5a — Drop sync API variants (~80 LOC src + ~80 LOC tests)
- **depends_on:** W4b.
- **scope:** Delete 6 sync entry points (`apply_changeset_sync`, `shell_sync`+`supports_sync`, `layer_path_from_{relative,absolute}`, `reset_session_cache`, `PathspecGitignoreOracle.filter_ignored` variant, `publish_changes`); delete matching test files; `live_e2e_test/sandbox/overlay/native/*` (3 files).
- **acceptance_tests:** `.venv/bin/pytest backend/tests -q -k "sync"` either passes or shows only intentional deletions; ruff clean.
- **risk_level:** T1 (deletions only).
- **rollback_plan:** `git revert`.

### PREP-5b — Wave-5b pre-flight report (BLOCKING DELIVERABLE per §14)
- **depends_on:** W5a.
- **scope:** Commit `.planning/wave-5b-preflight.md` classifying `result_projection.py` (87), `shell_runner.py` (181), `workspace_server.py` (173) as THIN or REAL-LOGIC per §14 template. Commit message must begin `wave-5b-preflight:`. Empirically expected verdict: all 3 KEEP.
- **acceptance_tests:** File exists; commit-message prefix verified by `check_wave5b_preflight.sh`; W5b primary commit must reference this SHA in body.
- **risk_level:** T1 (analysis only).
- **rollback_plan:** N/A (informational).

### W5b — Inline confirmed-thin daemon/service wrappers
- **depends_on:** PREP-5b.
- **scope:** Inline `service/layer_stack_client.py` (85) + `service/workspace_binding.py` (38) into `service/occ_backend.py` (115 → ~238 LOC projected). §14-gated: inline any of the 3 unverified files only if pre-flight reports THIN.
- **acceptance_tests:** `wc -l backend/src/sandbox/runtime/daemon/service/occ_backend.py` ≤500 (or ≤600 if §14 inlines all 3 AND no split commit); unit + integration green; AC #11 grep gate (`wc -l ... | awk '$1>600'` empty).
- **risk_level:** T2 (daemon coupling tightening).
- **rollback_plan:** `git revert`; pre-flight report stays as documentation.

### W5c — Contract / changeset multi-file collapse
- **depends_on:** W5b.
- **scope:** `execution/contract/{request,result,ports,spec}.py` → `execution/contract.py` (post-W2 path); `occ/changeset/{builders,prepared,types}.py` (3 files, 439 LOC) → 2 files (`types.py` model+builders, `prepared.py` kept for 9 ext refs).
- **acceptance_tests:** Import smoke `from sandbox.execution.contract import CommandExecRequest, CommandExecResult, MountMode, OCCMutationClient, SnapshotManifest, ShellProcessResult`; unit + integration green.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W6 — Internal Protocol thinning
- **depends_on:** W5c.
- **scope:** Replace single-impl Protocols in `occ/ports.py` (6 Protocols, ~95 LOC), `layer_stack/protocols.py` (5 Protocols, ~77 LOC), `occ/client.py::OccMutationService` (~25 LOC) with `TYPE_CHECKING` imports of concrete classes. Keep `WorkspaceBindingSnapshot` dataclass.
- **acceptance_tests:** Unit tests green; static-type check (`mypy` or `pyright`) clean — duck-typed safe, type-checked fragile.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W7a — `api/_impl/{read,write,edit}.py` consolidation
- **depends_on:** W6.
- **scope:** Collapse 3 modules (171 LOC) into `api/_impl/_run_verb.py` (~50 LOC) + 3 thin entry files (~10 LOC each) keeping `transport: SandboxTransport | None = None` kwarg verbatim. **Add regression test:** `_run_verb(spec, transport=sentinel)` invokes `sentinel.call(...)` exactly once.
- **acceptance_tests:** Grep gate `grep -rn "transport=DaemonSandboxTransport" backend/src/sandbox/api/_impl/` returns 0; sentinel test passes; targeted run of `grep -rn "transport=.*Mock\|transport=.*Fake" backend/tests` hits all pass.
- **risk_level:** T2 (Scenario E — silent mock-to-real-transport flip).
- **rollback_plan:** `git revert`; sentinel regression test catches the failure mode.

### W7b — Daemon handler tool trio extraction
- **depends_on:** W7a.
- **scope:** Extract `_with_snapshot_lease()` async ctx + `_classify_and_dispatch()` skeleton to `daemon/handler/tools/_common.py`; shrink each of `read.py`, `write.py`, `edit.py` by ~20 LOC.
- **acceptance_tests:** Unit + integration tests green.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W7c — Daytona client dedup + shutdown trim
- **depends_on:** W7b.
- **scope:** Extract `_acquire_cached_client(factory_cls)` helper shared between `async_client.py` + `sync_client.py`; **cache key MUST be `(factory_cls, credential_hash, target)`** with `assert factory_cls in (Daytona, AsyncDaytona)`. Compress `shutdown.py` 91 → ~35 LOC. **Add regression test:** sync + async clients back-to-back are distinct objects with correct concrete types.
- **acceptance_tests:** Regression test passes; manual live_e2e on **real Daytona** exercising both sync and async paths in same process; `provider.create()` 60s timeout per memory `daytona_pending_build_root_cause.md`.
- **risk_level:** T3 (Scenario F — 300s hang failure mode).
- **rollback_plan:** `git revert`; cache key includes `factory_cls` so reverse split is mechanical.

### W8a — `api/{lifecycle,transport,protocol,discovery,preview_urls,timeouts}.py` inlines
- **depends_on:** W7c.
- **scope:** ~155 LOC across 6 files; merge `versioned_payload()` into `host/daemon_client.py`; consolidate `discovery`+`preview_urls`+`lifecycle` remainder into `api/_control.py`; replace `SandboxTransport` Protocol with `Callable[..., Awaitable[dict]]`. **Pre-Wave-8a hard check:** `grep -rn "from sandbox.api.lifecycle" backend --include='*.py' | grep -v /sandbox/`; if hits exist, include a Wave-2-equivalent codemod step in this wave.
- **acceptance_tests:** Pre-wave grep gate passes (or codemod runs); unit + import smoke green; `sandbox.api` public symbol superset preserved (`dir(sandbox.api)` ⊇ pre-refactor).
- **risk_level:** T2 (public-adjacent surface).
- **rollback_plan:** `git revert`.

### W8b — `command_exec/strategies/registry.py` inline (parallelizable)
- **depends_on:** W8a.
- **scope:** Inline 2-strategy `StrategyRegistry.bootstrap()` as a 4-line tuple in `execution/workspace/mount.py`; inline `is_available(mode)` (1 non-test caller).
- **acceptance_tests:** Unit tests green; ruff clean.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W8c — `rpc/dispatcher.py register_op` cleanup (parallelizable)
- **depends_on:** W8a.
- **scope:** Inline 20-op `OP_TABLE` dict directly; **keep** `register_op` for plugin pipeline (plugin extensibility = user-protected).
- **acceptance_tests:** Unit + `test_daemon` slice green.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W8d — `occ/maintenance.py NoopMaintenancePolicy` inline (parallelizable)
- **depends_on:** W8a.
- **scope:** Replace constructor default with `if self._maintenance is None: return {}` guard at call site.
- **acceptance_tests:** `test_occ` slice green.
- **risk_level:** T1.
- **rollback_plan:** `git revert`.

### W9 — occ/stage shared logic + small inlines
- **depends_on:** W8b, W8c, W8d.
- **scope:**
  - 9a: Extract `_apply_edit_content` (~30 LOC duplicated) → `occ/stage/_edit.py`; move `_with_timings` (~6 LOC) → `occ/stage/policy.py`; remove dead-`Optional` branches (~30 LOC).
  - 9b: Inline `overlay/factory.py` (13 LOC, 1 caller); `execution/workspace/capture.py` (34 LOC, 1 caller); drop `invoker.py:97-105` re-sanitization + speculative comment.
- **acceptance_tests:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ -q` green; full unit + integration green.
- **risk_level:** T2 (occ staging path is hot).
- **rollback_plan:** `git revert`. NOTE: deeper `direct.py`↔`gated.py` merge (advisor-flagged 200–300 LOC win) explicitly OUT OF SCOPE; deferred per §15 ADR follow-up.

---

## 5. Merge Queue & Integration Policy

Per skill spec (rebase, dependency block, integration re-run):

1. **Branch model:** integration branch `codex/sandbox-reframe` (per §14 hook). Each unit is a feature branch off integration, rebased before merge.
2. **Dependency block:** A unit MUST NOT merge until every entry in `depends_on` has merged into integration AND its acceptance gate passed.
3. **Rebase rule:** Before queuing, rebase the unit branch onto integration HEAD. Re-run unit's acceptance gate post-rebase.
4. **Post-merge integration test:** After each merge, run the **per-wave gate template** from RFC §8.3.5:
   - `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q`
   - `.venv/bin/ruff check backend/src/sandbox`
   - 10-module import smoke
   - 600-LOC ceiling check
5. **Codemod budget tracking:** After W2 lands, the 209-import budget is consumed. Any later wave that introduces new import-path churn must add to its own codemod step (e.g., W5c contract collapse, W8a if pre-grep hits).
6. **Bench-baseline contract:** After W2, W3, W5c, W9, re-run `bench_sandbox_e2e.py --commands 10`; reject the wave if svc.cmd p50 regresses >5% vs `baseline.json` captured in PREP-0b.
7. **AC #11 hard cap:** After every merge, the 600-LOC ceiling check is a merge-blocker. W5b's projected occ_backend.py size (~238 LOC firm, up to ~680 LOC if §14 inlines all 3) is the canonical risk; pre-flight gates this.
8. **Wave-5b special gate:** Per §14 enforcement chain — W5b primary commit body MUST contain `Refs: <pre-flight-sha>`, OR the `check_wave5b_preflight.sh` hook rejects.

---

## 6. Integration Risk Summary

| Risk | Severity | Wave | Mitigation owner |
|---|---|---|---|
| **Daemon fails to boot post-rename** (tar arcname / `-m` path / `RUNTIME_SCRIPT_DIR` value missed) | CRITICAL | W3 | 5 path-shaped grep gates + live_e2e against real provider w/ 60s `provider.create()` timeout |
| **Codemod rewrites string literals or comments** (audit event names, log channels) | HIGH | W2, W3 | libcst `ImportFrom`-only; explicit string-literal allowlist (`"sandbox.overlay.executed"`); 4 manual surgical edits at named line numbers |
| **Test-mock seam flips to real transport silently** (Scenario E) | HIGH | W7a | Sentinel regression test in same commit; post-wave grep `transport=DaemonSandboxTransport` = 0 |
| **Daytona sync/async cache cross-contamination** (Scenario F: 300s hang) | HIGH | W7c | Cache key `(factory_cls, credential_hash, target)` w/ `assert`; live_e2e exercises both paths in same process |
| **`occ_backend.py` overflow >600 LOC** (Scenario G) | MEDIUM | W5b | §14 pre-flight blocks commit; W5b spec mandates split if projection >500 LOC |
| **Internal codemod miss in sandbox/ siblings** (Scenario D — relative-import form) | MEDIUM | W1.5, W2, W3 | libcst walks both absolute + relative; per-wave residual greps as merge-blocker |
| **Public `sandbox.api` symbol drift** | MEDIUM | W7a, W8a | AC #4 — `dir(sandbox.api)` superset check; pre-W8a `grep -rn "from sandbox.api.lifecycle"` external-consumer check |
| **Bundle-hash cache invalidation** | LOW (observable, one-time) | W3 | Document in commit message; on-call alerted; one-time re-upload per running sandbox |
| **Logger-name observability change** (`sandbox.runtime.daemon.*` → `sandbox.daemon.*`) | LOW | W3 | Document in commit message; ops to update any log filters |
| **20% LOC reduction target unreachable** | INFO (already accepted) | — | ADR §15 documents 7.4% realistic ceiling; AC #9 floored at 1,222 LOC |

---

## 7. Recovery Policy (per skill spec)

If a unit stalls or its acceptance gate fails after rebase:
1. **Evict** from active queue (revert HEAD if already merged into integration; close branch if not).
2. **Snapshot findings** — append to `.planning/sandbox-reframe-execution-log.md` (executor responsibility): which gate failed, residual greps, failing test names, bench delta.
3. **Regenerate narrowed scope** — if W2 fails on a specific subfolder codemod, split into W2a (`command_exec→execution`) + W2b (`overlay→execution.overlay`) and re-queue.
4. **Retry with updated constraints.**

Special case — W3 post-merge daemon-boot failure: **immediate fast-revert** (do not iterate forward). Per memory `daytona_pending_build_root_cause.md`, daemon-boot bugs manifest as 300s `provider.create()` hangs visible only at e2e; revert is cheaper than diagnosis under pressure.

---

## 8. Pipeline Stage Status & Handoff

| Stage | Status | Artifact |
|---|---|---|
| 1. RFC intake | ✅ | This doc §1 |
| 2. DAG decomposition | ✅ | This doc §2 |
| 3. Unit assignment (specs + tiers) | ✅ | §3 + §4 scorecards |
| 4. Unit implementation | ⏳ | Executor — PREP-0 first |
| 5. Unit validation | ⏳ | Per-unit acceptance gates in §4 |
| 6. Merge queue and integration | ⏳ | Policy in §5; integration branch `codex/sandbox-reframe` |
| 7. Final system verification | ⏳ | RFC §8.4 behavioral-diff definition + AC §7 + §13 |

**Next action for executor:** land PREP-0 and PREP-0b as separate commits on integration branch before starting Wave 0.
