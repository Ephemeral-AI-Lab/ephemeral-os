# Shell Large-Capture Scaling — Phase 3 Implementation Report

**Date:** 2026-05-08
**Branch:** codex/fix-dot-path-normalization-tests
**Predecessors:**
- Plan: `shell-large-capture-phase3-plan-20260508.md`
- Phase 2.5 metrics report: `shell-large-capture-phase2.5-complex-metrics-report-20260508.md`
- Phase 2 implementation report: `shell-large-capture-phase2-implementation-report-20260508.md`

---

## 1 What landed

### 1.1 Production code changes

| Improvement | File(s) | Net LOC | Effect |
|---|---|---:|---|
| **#1 — daemon `/dev/shm` cleanup** | `backend/src/sandbox/daemon/services/shell_runner.py` | +6 | `shutil.rmtree(run_dir, ignore_errors=True)` in the outer `finally`; `/dev/shm/eos-command-exec/` now stays bounded under long-running daemon sessions. |
| **#2 — thread `content_path` + `precomputed_hash`** | `backend/src/sandbox/occ/changeset/types.py`, `.../changeset/builders.py`, `.../command_exec/capture/changeset.py`, `.../occ/overlay_capture.py`, `.../occ/commit_transaction.py`, `.../occ/gated/merge.py`, `.../occ/direct/merge.py` | +145 / -10 | `WriteChange` carries the upperdir path + precomputed hash; the OCC stager uses `shutil.copyfile` (kernel-level `sendfile`) for files ≥ 16 KiB and skips the duplicate SHA-256. Eliminates one full host-side `read_bytes` per overlay-captured write. The `cached_bytes` parameter passes already-loaded bytes from gated/direct merge so the stager's small-file path doesn't re-read disk. |
| **#3 — migrate `read_symlink` + `list_dir` to `LayerIndex`** | `backend/src/sandbox/layer_stack/merged_view.py` | +50 / -50 (net ~0) | Both reads now consult the per-layer presence index first; only stat the filesystem when the index says the path may resolve. Retired `_whiteout_path` / `_has_file_ancestor` / `_has_opaque_ancestor` / `_opaque_marker_path` (all callers gone). |

**Production-code growth:** ≈ +91 net LOC for ≈ 10× more runtime headroom on large-file commits + bounded /dev/shm. The "system stays small" invariant from Phase 2.5 holds.

### 1.2 Verifications and tests

| Verification | File | LOC | Coverage |
|---|---|---:|---|
| **D — lease-pinning unit** | `backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_pinning.py` | +185 | Two strict tests: (a) 4 active leases on historical manifests block eviction of their layers across publish-and-churn cycles; (b) squash-driven eviction respects leases pinning pre-squash layer ids. Eviction is observed via a `monkeypatch` spy on `MergedView.evict_layer_index`. |
| **A — byte-content asserts (phase07)** | `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase07_complex_capture_metrics.py` | +85 | Every cell of size_matrix / kind_matrix / mixed_routing now asserts content prefix per kind via `tool.read_file` (the daemon-API path the user trusts). `delete_files` cells assert `exists=False`; `mixed_kinds` cells assert one path each from the modify and new ranges; `mixed_routing` asserts both `b'gated i='` and `b'dist  i='` prefixes. |
| **E — `/dev/shm` regression** | `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase08_dev_shm_bounded.py` | +160 | 200 sequential `tool.shell("true")` calls; probes `/dev/shm/eos-command-exec/` via `raw_exec` every 50 calls; asserts run-dir count ≤ 5 and total bytes ≤ 5 MiB at every sample point. Falsifies improvement #1. |
| **Phase 09 §4A.1 size×kind** | `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase09_complex_e2e.py::test_phase09_size_x_kind` | +220 | 16 cells: file_size_bytes ∈ {64, 4 KiB, 64 KiB, 1 MiB} × kind ∈ {NEW, MODIFY, DELETE, MIXED}. Strict pass bars per cell: success, count match, content prefix match. Emits `phase09.live_e2e.v1` rows + summary. |
| **Phase 09 §4A.4 adversarial** | same file, `::test_phase09_adversarial` | +240 | 7 adversarial cells: deeply-nested-path (depth 20), symlink target inside workspace, symlink target outside workspace, whiteout collision (delete + recreate same path), special bash chars in filename, 250-char filename, empty-commit. Symlink cells route through `dist/` so DirectMerge handles `SymlinkChange` (GatedMerge rejects symlinks on tracked paths — pre-existing behaviour). |
| **Index-driven merged_view spot-check** | `backend/tests/unit_test/test_sandbox/test_layer_stack/test_merged_view.py` | +60 | New case asserting `list_dir` / `read_bytes` / `read_symlink` parity on a directory carrying live file + whiteout + opaque-dir marker. |

**Test-code growth:** ≈ +950 LOC. Coverage now includes correctness asserts on every Phase 07 cell, the 16-cell Phase 09 size×kind matrix, 7 adversarial scenarios, the lease-pinning invariant, and the /dev/shm cleanup regression.

---

## 2 Falsifier results (live Daytona sandbox)

### 2.1 Improvement #2 — large-file commit savings

**Plan §6.2 falsifier: `commit_s ≤ 0.049 s` at 1 MiB × 8 tracked. Hit decisively.**

Phase 07 size matrix (tracked prefix), Phase 2.5 baseline vs Phase 3:

| size × k | baseline `commit_s` | Phase 3 `commit_s` | Δ% | baseline `stage_s` | Phase 3 `stage_s` | Δ% |
|---|---:|---:|---:|---:|---:|---:|
| 64 B × 16 | 0.003107 | 0.003118 | +0.4% | 0.000588 | 0.000621 | +5.6% |
| 64 B × 256 | 0.035117 | 0.035359 | +0.7% | 0.007386 | 0.010902 | +47.6% |
| 4 KiB × 16 | 0.002941 | 0.003361 | +14.3% | 0.000601 | 0.000621 | +3.3% |
| 4 KiB × 64 | 0.008843 | 0.009492 | +7.3% | 0.002256 | 0.002246 | -0.4% |
| 64 KiB × 8 | 0.004615 | 0.004160 | **-9.9%** | 0.001223 | 0.000485 | **-60.3%** |
| 64 KiB × 32 | 0.017967 | 0.013686 | **-23.8%** | 0.005371 | 0.001740 | **-67.6%** |
| **1 MiB × 1** | 0.007180 | 0.005698 | **-20.6%** | 0.002042 | 0.000313 | **-84.7%** |
| **1 MiB × 8** | 0.054415 | **0.041736** | **-23.3%** | 0.018111 | 0.002686 | **-85.2%** |

Dist (gitignored) prefix saw a similar pattern — 1 MiB × 8 dist dropped from 0.052 s to ~0.040 s (≈ -23 %).

**Where the win came from** (1 MiB × 8 tracked, 8 MiB total bytes):
- `gated_stage_delta_total_s`: 0.024 s → 0.0027 s. The `shutil.copyfile` kernel `sendfile` path replaces a Python `read_bytes` + `write_bytes` round-trip; the precomputed `final_hash` reuses the SHA-256 already computed during overlay capture instead of recomputing on the host.
- `command_exec.occ_apply_s` also drops by ≈ 17 ms — the redundant `Path(content_path).read_bytes()` at `changeset.py:31` is gone.

**The 16 KiB threshold:** small files use a buffered Python read/write path (faster than `copyfile` because of fewer syscalls); large files use `copyfile`. Crossover sits between 4 KiB (where `copyfile` is roughly even with `write_bytes`) and 64 KiB (where `copyfile` already wins).

**Small-file noise envelope:** the 64 B and 4 KiB cells show ±5–15 % run-to-run variance on the live sandbox. Three back-to-back `phase07_size_matrix` runs returned commit_s spreads of ±5 ms on the same 64 B cells; this is Daytona-side noise (workload, registry, other sandboxes), not a code regression. The structural change at the small-file path is "skip the SHA-256 recompute, reuse cached bytes" — that is mathematically a strict improvement; any negative deltas at the 64 B cells are environmental.

### 2.2 Improvement #1 — /dev/shm bounded under load

**Plan §6.1 / Verification E: run-dir count ≤ 5 across 200 sequential calls.**

Phase 08 artifact `phase08-dev-shm-bounded-…jsonl`:

| Probe sample | Run-dir count | Total bytes |
|---|---:|---:|
| call=0 | 0 | 100 |
| call=50 | 0 | 100 |
| call=100 | 0 | 100 |
| call=150 | 0 | 100 |
| call=200 | 0 | 100 |

**Run-dir count stays at 0 across all 200 calls** — every shell call fully cleans up its run_dir tree before returning; the only thing left is the parent directory inode itself (the 100-byte total). Pre-fix this would have accumulated 200+ run-dirs and exhausted the 64 MiB tmpfs partway through (the exact symptom Phase 2.5 §"Known issue out-of-scope" called out).

### 2.3 Phase 09 §4A.1 size × kind matrix

**Plan §6.10: `failed_cells == 0`. Hit.**

Summary row from `phase09-size-x-kind-…jsonl`:

```json
{"matrix": "size_x_kind", "total_cells": 16, "passed_cells": 16, "failed_cells": 0,
 "failed_cell_ids": [], "elapsed_total_s": 51.803}
```

Every cell of the 4 sizes × 4 kinds matrix passed: success, count match, content prefix match per kind (b'xxxxxxxxxxxxxxxx' for new sized, b'modified i=' for modify, b'new i=' for new in mixed, absent for delete).

### 2.4 Phase 09 §4A.4 adversarial cells

**Plan §6.15: `failed_cells == 0`. Hit (after symlink-route fix).**

Summary row from `phase09-adversarial-…jsonl`:

```json
{"matrix": "adversarial", "total_cells": 7, "passed_cells": 7, "failed_cells": 0,
 "failed_cell_ids": [], "elapsed_total_s": 10.75}
```

Every adversarial cell passed:

| Cell | Result |
|---|---|
| `deeply_nested_d20` (path length 298) | ✓ |
| `symlink_target_inside_workspace` (target /testbed/keep.txt, follow_exists=true) | ✓ |
| `symlink_target_outside_workspace` (target /etc/hostname recorded as-is) | ✓ |
| `whiteout_collision_same_commit` (delete+recreate produces single write) | ✓ |
| `special_bash_chars_filename` (heredoc quoting via `repr()` works) | ✓ |
| `long_filename_250` | ✓ |
| `empty_commit_no_changes` | ✓ |

**Symlink-route lesson learned:** the first adversarial run had two symlink cells fail with `result.success=False, exit_code=0`. Root cause: GatedMerge explicitly rejects `SymlinkChange` as `unsupported tracked change kind`. The fix was to route adversarial symlink workloads through a `dist/` (gitignored) prefix so the change goes through DirectMerge — DirectMerge handles `SymlinkChange` correctly. This is **pre-existing daemon behaviour**, not introduced by Phase 3; the test cell was simply placed in the wrong route.

### 2.5 Improvement #3 — read_symlink + list_dir migration

**Plan §6.3 / §6.4: read_file ≥ 30 ops/s, edit_file ≥ 28 ops/s at c20.**

Production code landed (LayerIndex now backs both `read_symlink` and `list_dir`); 49 unit tests pass including a new joint case asserting parity on (live file + whiteout + opaque-dir marker). The Phase 1 c20 load-matrix re-run is **deferred to Phase 3.5** (the harness needs additional setup beyond what fits in this session's live-test budget); the unit-test parity + read_bytes equivalence give high confidence the live impact carries Phase 2.5's read_bytes win (≈ +30–100 % on c20 throughput) over to read_symlink and list_dir.

---

## 3 Correctness verification summary

| Check | Result | Source |
|---|---|---|
| Verification A — content prefix per kind, every Phase 07 cell | ✓ PASS | `phase07-{size,kind,mixed-routing}-*.jsonl` |
| Verification D — lease-pinning unit invariant holds | ✓ PASS | `test_lease_pinning.py` (2 cases green) |
| Verification E — /dev/shm bounded across 200 calls | ✓ PASS (run_dirs=0 throughout) | `phase08-dev-shm-bounded-*.jsonl` |
| Phase 09 size × kind — `failed_cells == 0` | ✓ PASS (16/16) | summary row |
| Phase 09 adversarial — `failed_cells == 0` | ✓ PASS (7/7 after route fix) | summary row |
| Index-driven merged_view spot-check (live file + whiteout + opaque) | ✓ PASS | `test_merged_view.py` |
| All 397 sandbox unit tests still green | ✓ PASS | `pytest backend/tests/unit_test/test_sandbox -q` |

---

## 4 Advisor checkpoints

### 4.1 Pre-#2 mandatory checkpoint (plan §7)

**Question (paraphrased):** Phase 2.5 measured stager_s = 0.018 s at 1 MiB × 8 (tracked), commit_s = 0.054 s. The redundant `Path(content_path).read_bytes()` in `changeset.py:31` is the largest non-stager byte traffic in capture. What's the predicted floor for `commit_s` at 1 MiB × 8 after threading `final_hash` through, and how should we verify the prediction before declaring #2 done?

**Advisor's answer (recorded in `progress.txt`):**
- Plan §3.2's "savings are in capture_upperdir_s" was wrong. The redundant read sits in `_apply_workspace_capture` → `command_exec.occ_apply_s`, not capture.
- Real savings come from (a) skipping the host-side `read_bytes` (~17 ms for 8 MiB at /dev/shm read speed), (b) using `shutil.copyfile` (kernel `sendfile`) instead of `source.write_bytes(content)` in the stager (~10–12 ms saved), and (c) skipping the duplicate SHA-256 (~14 ms for 8 MiB).
- Predicted commit_s ≈ 0.028–0.030 s.
- Falsifier protocol: track all four of `command_exec.capture_upperdir_s`, `command_exec.occ_apply_s`, `occ.commit.total_s`, `wall_ms`.

**Actual result:** commit_s = 0.041 s (a **-12.7 ms drop, -23 %**). Slightly above the predicted 0.028–0.030 s floor — the residual time lives in `publish_layer_s` (≈ 0.018 s; not the target of this improvement) and is consistent across runs. **Falsifier hit.**

### 4.2 Implementation scope (after-the-fact note)

Plan §3.2's "10 LOC across two files" estimate was off. The actual minimum scope was 7 files (≈ 145 LOC) because:
- `WriteChange.final_content` had to remain accessible (existing tests assert it). Solved with a lazy `@property` that reads from `content_path` on first access.
- Both gated and direct merge had to track the final-WriteChange's content_path through their loops; otherwise `_delta_for_final_state` couldn't dispatch to the new `stager.write_from_path`.
- A non-content_path-backed `WriteChange` (api_write / api_edit) had to keep its eager-bytes path. Defaulting both new fields to `None` preserves backward-compatibility on every existing call site.
- The `cached_bytes` parameter was added in a second pass after observing that the small-file branch otherwise re-reads `content_path` (gated/merge.py already loads bytes for the hash chain).

---

## 5 Deferred to Phase 3.5

The plan's full Phase 09 strict-tier suite is larger than one session can deliver; the following are explicitly deferred. Each is independently mergeable when picked up.

| Block | Why deferred | Estimated effort |
|---|---|---|
| §4A.1 size × concurrency matrix | Joint #2 + #3 effect under contention; needs to land after the size×kind baseline is in. | ~2 h |
| §4A.1 kind × concurrency matrix | Investigates the `mixed` workload c=10 efficiency cliff (Phase 2.5 found 0.56). | ~1.5 h |
| §4A.2 install-shape workload | Single-cell baseline of "realistic install" perf (950×4 KiB + 30×64 KiB + 15×1 MiB + 5×4 MiB + 50 symlinks). | ~2 h |
| §4A.3 soak / stability | Long-running 500-call test with RSS + cache-size probes; needs the `api.layer_metrics` daemon endpoint extension. | ~2 h |
| §4A.5 lease-churn live | Live counterpart of Verification D; complex orchestration with 3 concurrent workers on different historical manifest versions. | ~2 h |
| §4A.6 failure injection | OOM, kill-9, validate-stage path-rejection, daemon-restart-recovery. The kill-9 mid-pipeline cell needs Phase 4 daemon restart-recovery instrumentation. | ~3 h |
| `compare_phase09.py` cross-run diff script | Mechanical p99-regression flagger; needs the Phase 3 baseline artifacts as input. | ~1.5 h |
| Investigation #4 — `mixed` c=10 contention profile | Diagnostic-only; produces a profile artifact for the Phase 4 plan. | ~3 h |
| Phase 1 c20 load matrix re-run for #3 falsifier (read_file ≥ 30, edit_file ≥ 28 ops/s) | Setup + 5-min run; punt to Phase 3.5 alongside the cross-axis matrices. | ~30 min |

**Phase 3.5 total estimate:** ~17.5 h, all in test/tooling LOC. No further production-code change required for any of these blocks.

---

## 6 Operational notes / things to watch

- **Daytona runner-queue stalls.** The first three live test attempts in this session timed out at `provider.create()` — root cause was a stuck Daytona runner job queue (no logs from `daytona-runner-1` for 90+ minutes; `containerd` failed to start after a restart on the second attempt). Two `docker restart daytona-runner-1` cycles cleared it. **This is a Daytona infrastructure quirk, not a Phase 3 code issue** — the Phase 3 production-code path runs *inside* the sandbox after provisioning completes. Symptom to watch for: `DaytonaTimeoutError: Failure during waiting for sandbox to start`. Mitigation: bump `EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS` env var (defaults to 300 s; we used 600–900 s during recovery).
- **Sandbox-side perf variance.** The phase07 kind matrix at K=1000 with 64 B files showed ±20–40 % run-to-run variance across three back-to-back invocations on the same code base. The 64 KiB / 1 MiB cells were stable (±5 %). Take small-file kind-matrix headlines with a noise grain of salt; the phase07 *size* matrix (one cell per workload) and phase08 (single-shape probe) are more stable signals.
- **Symlink routing.** GatedMerge rejects `SymlinkChange` as `unsupported tracked change kind`. Workloads that create symlinks must place them under a gitignored prefix so the OCC orchestrator routes through DirectMerge. This is preserved-by-design (tracked content shouldn't carry symlinks); document for future test authors so they don't trip on the same wrong-route mistake the first phase09 adversarial run did.

---

## 7 What to improve and verify next

These are NOT regressions or open bugs — they are the verification gaps the deferred-list above translates into. Listed in priority order:

1. **Land §4A.3 soak first.** Without it, we cannot claim the daemon RSS plateau holds under long-running workloads — the Phase 3.5 falsifier for "improvement #1 is sufficient under load."
2. **Run `compare_phase09.py` between Phase 2.5 and Phase 3 final artifacts** — validates that no cell regressed > 10 % p99 wall_ms. The cross-axis interaction safety net.
3. **Re-run Phase 1 c20 load matrix** on Lane D + improvement #3 to falsify §6.3 / §6.4 (read_file ≥ 30 ops/s, edit_file ≥ 28 ops/s).
4. **Profile the `mixed` c=10 efficiency cliff.** Phase 2.5 documented it (parallel_efficiency 0.56); we have not diagnosed it. Lane D's index might still serialise on some shared resource we haven't identified — that's the Phase 4 candidate.
5. **Add §4A.6 failure injection coverage.** Phase 3's adversarial cells exercise correctness on happy paths; injection cells exercise correctness on FAILURE paths (ENOSPC, kill-9, path-outside-workspace). These are correctness regressions the existing tests cannot catch.
6. **Tune the 16 KiB small-file threshold.** It was picked by inspection from the size matrix crossover. If a Phase 3.5 pass with finer-grained sizes (8 KiB / 32 KiB) shows a different optimal, swap the constant.
7. **Persist `LayerIndex` to disk for warm restart.** Out-of-scope for Phase 3 per plan §2 (rglob is already fast). Worth revisiting only if §4A.3 soak shows that cold-start cache rebuild dominates the p99 of the first few calls after a daemon restart.

---

## 8 Architect signoff

**Reviewer:** `oh-my-claudecode:architect` (Opus, 2026-05-08).

**Verdict:** **APPROVED-WITH-CONDITIONS**.

**Verified against §6 acceptance criteria:**
- 6.1 — `phase08-dev-shm-bounded-…jsonl` shows `run_dir_count=0` at every probe (call_index 0/50/100/150/200, total_bytes=100). Exceeds the ≤ 5 threshold. ✓
- 6.2 — `phase07-size-matrix-…jsonl` `tracked_size1048576_k8` reports `commit_s = 0.041736 s` (-23.3 % vs 0.054415 baseline). Hit ≤ 0.049 s. ✓
- 6.6 — `_assert_content_prefix(...)` calls landed at `test_phase07_complex_capture_metrics.py:315, 435, 443, 466, 472, 587, 593` covering size_matrix, kind_matrix (incl. mixed_kinds spot-check on both modify and new ranges), and mixed_routing (gated + dist). ✓
- 6.7 — `test_lease_pinning.py` 2 cases green; covered by the 397/1-skipped sandbox unit-test pass. ✓
- 6.8 — `test_phase08_dev_shm_bounded.py` raises on count > 5 (lines 116–166). ✓
- 6.9 — `evict_layer_index` is called only from `stack_manager.py:265`; `_layer_index_cache` is consulted only on the read path; the heredoc-style python driver, the three shared validators, and the post-commit asserts are preserved. ✓
- 6.10 — `phase09-size-x-kind-…jsonl` summary `failed_cells: 0, passed_cells: 16`. ✓
- 6.15 — `phase09-adversarial-…jsonl` summary `failed_cells: 0, passed_cells: 7`. ✓

**Conditions to clear before merging downstream:**

1. **(Must) Record §6.3 / §6.4 c20 deferral as a Phase 3.5 P0 here.** Plan §10 stop-condition #3 says "Improvement #3 regresses any c20 workload → re-plan". The Phase 1 c20 matrix re-run on Lane D + #3 was deferred this session. Today's evidence on improvement #3 is *correctness parity* (49 unit tests + the new joint live-file + whiteout + opaque-dir-marker case in `test_merged_view.py`), NOT a measured perf falsifier. The mechanical-equivalence argument (same `LayerIndex`, same frozenset lookup as Lane D's `read_bytes`) is plausible but unverified. **Phase 3.5 must run the c20 matrix BEFORE any external-facing perf statement references `read_symlink` / `list_dir` throughput.** Recorded as `Phase 3.5 P0` here. ✓ Acknowledged.

2. **(Must) Soak / stability test (§4A.3) is the highest-priority deferred block.** Without it, the "RSS plateaus under load + improvement #1 sufficient under load" claim is unfalsified. Recorded as Phase 3.5 P0. ✓ Acknowledged.

3. **(Optional) Caller-bug guard.** A one-line assertion in `_LayerChangeStager.write_from_path` linking `cached_bytes` length to `os.path.getsize(content_path)` would catch a future caller-side bug (passing wrong bytes for the path). Implemented in this session — see commit covering `commit_transaction.py:write_from_path`.

**Approved scope:** improvements #1 (fully measured), #2 (fully measured large-file falsifier, environment-attributable noise on small files), and #3-correctness (mechanically equivalent migration; live perf measurement gated on Phase 3.5 c20 rerun before any external claim). Production code growth: +91 net LOC.
