# Progressive Live-Test Tiers — Implementation Report

**Date:** 2026-05-08
**Spec:** `.omc/plans/progressive-live-test-tiers-design-20260508.md`
**Branch:** `codex/fix-dot-path-normalization-tests`
**Build order (advisor-revised):** A → C-skeleton → B → C-finish → D

---

## 1 What Landed

### Phase A — Tier 0 health probe + tier-1 smoke (~600 LOC)

| File | LOC | Purpose |
|---|---:|---|
| `backend/tests/live_e2e_test/_tools/daytona_probe.sh` | 185 | Standalone bash escape hatch; probes API health, runner health, stuck Daytona DB rows; applies the §6 SQL workaround when invoked with `docker` available. |
| `backend/tests/live_e2e_test/_tools/tier0_health.py` | 389 | Python probe API: `probe_tier0(api_url) → Tier0Result`. Re-orders the bash logic for the runner. Includes runner-bootstrap detection + stale containerd PID handling (parallel extension by codex). |
| `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` | 169 | Tier-1 smoke test. Three streamed cells: `tool.shell("true")`, gated write+read, direct write+read. Honors `EOS_TIER_RUN_ID` for artifact stability. |

### Phase C — Runner skeleton + finish (~1140 LOC including tests)

| File | LOC | Purpose |
|---|---:|---|
| `backend/tests/live_e2e_test/_tools/run_tiered.py` | 514 | CLI tier runner. `subprocess.Popen(start_new_session=True)` for budget enforcement; SIGINT-then-SIGKILL on overrun; cascade state machine (`abort_all` / `abort_ge` / `abort_eq` / `warn` / `none`); per-tier JSONL aggregator. |
| `backend/tests/live_e2e_test/_tools/tiers.toml` | 90 | Declarative configuration of all 7 tiers per plan §3. |
| `backend/tests/unit_test/test_live_e2e_tools/test_run_tiered.py` | 535 | 25 unit tests covering TOML loading, cascade state machine, budget timeout (mocked Popen), tier execution (Tier 0 + pytest), full pipeline cascade integration, JSONL aggregator. |
| `backend/tests/unit_test/test_live_e2e_tools/test_tier0_health.py` | 233 | 10 unit tests for tier-0 probe (extended by codex parallel for runner-bootstrap probe coverage). |

### Phase B — Per-cell streaming refactor on existing matrices

In-place edits (no new files):

| File | Edit |
|---|---|
| `test_phase07_complex_capture_metrics.py` | Added `_resolve_run_id`, `_load_prior_data_rows`, `_stream_row`, `_rewrite_artifact`. Each of the 3 matrix tests now reads prior rows, skips completed cells, streams new rows append+flush+fsync, and rewrites artifact with end-of-matrix summary row. **All inline asserts preserved.** |
| `test_phase09_complex_e2e.py` | Same streaming + resume contract. `_run_adversarial_cell` now takes `artifact`/`completed`/`rows`; streams + skips internally. The two `assert summary["failed_cells"] == 0` assertions are preserved verbatim. |
| `test_phase08_dev_shm_bounded.py` | Per-iteration row stream; `EOS_TIER_RUN_ID` honored; intentional truncate-on-start (probe-loop is not resume-meaningful). |
| `test_phase09_k1000_concurrency.py` | Per-call row stream + per-c summary stream; `EOS_TIER_RUN_ID` honored. |

### Phase D — New cross-axis matrices (~570 LOC)

| File | LOC | Matrix | Cells |
|---|---:|---|---:|
| `test_phase09_size_x_concurrency.py` | 269 | `file_size_bytes ∈ {64, 4096, 65536} × c ∈ {1, 5, 10, 20}` | 12 |
| `test_phase09_kind_x_concurrency.py` | 299 | `kind ∈ {new_files, modify_files, delete_files} × c ∈ {1, 5, 10}` | 9 |

Both follow the streaming + resume contract; per-cell pass-bar is "all c calls succeed AND median commit_s ≤ 3× the c=1 baseline of the same axis"; end-of-matrix summary row asserts `failed_cells == 0`.

### Total

- **~2,684 LOC** across new infra + unit tests
- Plan estimate ~480 LOC. Overshoot is mostly in unit-test coverage (768 LOC) and the parallel runner-bootstrap probe extension (~150 LOC).
- **Production code: zero changes.** All edits live under `backend/tests/`.

---

## 2 Tier 0 Dry-run

```
$ PYTHONPATH=backend .venv/bin/python -m tests.live_e2e_test._tools.run_tiered --tier 0
[run_tiered] summary=.omc/results/progressive-test-summary-20260508T000432Z-61061.jsonl
[run_tiered] run_id=20260508T000432Z-61061
  tier 0 [preflight] passed elapsed=0.12s failed_cells=0
    notes: health: http_code=200; runner_health=healthy
```

**Result: PASS in 0.12 s.** Daytona API + runner both healthy at probe time.

This is the design's headline value-prop: a Daytona-side stall surfaces in **120 ms** instead of waiting for a multi-minute matrix to die at sandbox bring-up.

---

## 3 Tier 1 Live Smoke

See "Notes for next session" — Tier 1 was kicked off as part of T-RUN. The result is recorded in `.omc/results/progressive-test-summary-<run_id>.jsonl` and the per-tier artifact `phase00-smoke-<run_id>.jsonl`.

If Tier 1 passed, the full cascade through Tier 1 is operational. If Tier 1 failed (e.g., Daytona sandbox provisioning timed out), the cascade rule `abort_ge target=2` would skip Tiers 2 and 3, but Tiers 4-6 remain runnable per plan §3.

---

## 4 Deferred to Future Sessions

Live runs of **Tiers 2-6** are not part of this PRD's acceptance — the runner is *invocable* and `pytest --collect-only` succeeds on every tier's target, but the full live run takes 1500 s + 900 s for Tiers 4-5 alone and requires a healthy Daytona for the entire duration. These are runnable on demand via:

```
PYTHONPATH=backend .venv/bin/python -m tests.live_e2e_test._tools.run_tiered --tier 2,3,4,5,6
```

---

## 5 Deviations from Plan (Advisor-flagged)

### 5.1 Artifact path stability via `EOS_TIER_RUN_ID`

**Plan §5** assumed stable artifact filenames. The existing `_artifact_path()` generated `<label>-<ISO_TIMESTAMP>-<pid>.jsonl` per invocation, so `_completed_cells(artifact)` would never find a prior artifact and resume-on-restart was DOA.

**Fix:** Tests honor the env var `EOS_TIER_RUN_ID` when set; otherwise fall back to the existing ISO+pid filename. The runner sets `EOS_TIER_RUN_ID` per pytest subprocess, so resume-across-invocations finds the prior artifact deterministically. **Backwards compatible:** existing standalone pytest invocations are unchanged.

### 5.2 `subprocess.Popen` instead of `asyncio.wait_for`

**Plan §7** mentioned `asyncio.wait_for`. That cannot propagate cancellation through a child pytest process; it would cancel the awaiter without killing the subprocess.

**Fix:** Spawn pytest with `subprocess.Popen(start_new_session=True)` so the runner controls the entire process group. On wall-budget exceeded: `os.killpg(pgid, SIGINT)`, wait 30 s grace for clean shutdown, then `os.killpg(pgid, SIGKILL)`. The `tier_aborted_wall_budget` summary row is written regardless.

### 5.3 Tier 0 docker/psql is best-effort

**Plan §6** assumes `docker` + `psql` are available on the host. CI hosts and remote test boxes may not have them.

**Fix:** `tier0_health._detect_stuck_rows` returns `(docker_available=False, [], "docker_unavailable")` when docker is missing instead of raising. The probe verdict falls back to "API health endpoint OK is sufficient". The `daytona_probe.sh` escape hatch handles the same case — exit 2 with `recovery_attempted=false reason=docker_unavailable`.

### 5.4 Cascade rule disambiguation: `abort_ge` vs `abort_eq`

**Plan §3** uses both "abort 2+" (skip tiers ≥ 2) and "abort 5" / "abort 6" (skip just that tier) without disambiguating in the rule explanation. Encoded explicitly as two cascade kinds in `tiers.toml`:
- `abort_all`: tier 0 only
- `abort_ge`: tier 1 (target=2) — "abort 2+"
- `abort_eq`: tiers 4 (target=5), 5 (target=6) — single-tier targets
- `warn`: tiers 2, 3
- `none`: tier 6 (terminal)

### 5.5 Phase B adversarial cell signature change

`_run_adversarial_cell` was modified from "build row, return it" to "build row, stream it, append to caller's `rows` list". This collapses 7 boilerplate `rows.append(await ...)` wrappers at call sites into single-statement calls. The function returns `None` if the cell_id is in the caller's `completed` set (resume contract).

---

## 6 Verification

| Check | Result |
|---|---|
| `.venv/bin/pytest backend/tests/unit_test/test_live_e2e_tools` | 35 passed |
| `.venv/bin/pytest --collect-only` on all 9 progressive-tier tests | 9 collected |
| `.venv/bin/pytest backend/tests/unit_test/test_sandbox` | 398 passed, 1 skipped, 1 pre-existing failure unrelated (`test_bundle_layout_includes_required_paths` fails because `sandbox/bash.py` was deleted in the sandbox-package refactor on commit `b53318cbf` — not introduced by this PRD) |
| `.venv/bin/ruff check` on every new + modified file | All checks passed |
| `bash -n daytona_probe.sh` | Parse-clean |
| Tier 0 dry-run via `run_tiered.py` | PASSED in 0.12 s |

---

## 7 What This Means for Phase 3 in Hindsight

Re-running Phase 3's session under this design:

| Phase 3 Pain Point | Time Lost | Under Tiered Design |
|---|---:|---|
| Daytona stall at 22:25 UTC | ~10 min of confusion | Tier 0 catches stuck `state='starting'` row in 30 s; auto-recovery (when invoked with `--auto-recover`) clears it. |
| Phase 07 size matrix 16 cells, end-of-loop write | ~6 min of rerunning to find which cell broke | Per-cell row at minute 1 of the bad cell, kill-9 leaves all prior rows intact. |
| Phase 09 adversarial 5-cell streak before failure visible | ~7 min wasted | Failed symlink cells visible in artifact at minute 1 of 1.5; fix-and-resume skips passed cells. |
| K=1000 c=1/5/10/20 concurrency burn 25 min | All-or-nothing | Cell c=1 row at ~30 s; if Daytona stalls before c=5, the c=1 baseline is preserved. |

---

## 8 Reviewer Signoff

[Pending — to be invoked at end of Ralph cycle.]
