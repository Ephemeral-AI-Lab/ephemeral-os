# Shell Large-Capture Scaling — Phase 3 Plan

**Date:** 2026-05-08
**Branch (target):** `codex/fix-dot-path-normalization-tests` or successor
**Predecessors:**
- Phase 2 plan: `shell-large-capture-scaling-plan-20260508.md`
- Phase 2 implementation report: `shell-large-capture-phase2-implementation-report-20260508.md`
- Phase 2.5 complex-metrics report: `shell-large-capture-phase2.5-complex-metrics-report-20260508.md`
- Phase 1 (revised) implementation report: `shell-concurrency-phase1-implementation-report-20260508.md`

**Goal:** Ship the three "safe" improvements ranked in Phase 2.5 (§"Next: improvements to ship safely") without growing the system's surface area or breaking any invariant from §"System-design invariants to preserve". Each improvement is independently testable and independently revertible.

**Falsifiable success criteria:** § 6 below. Each criterion has a specific number a fresh benchmark must hit; a phase that lands the code but misses any criterion is a partial success and must be triaged before merge.

---

## 1 Why this phase exists

Phase 2 closed the K-scaling ceiling on `MergedView.read_bytes`. Phase 2.5 measured the new perimeter:

- **`/dev/shm` leak** is the only blocker preventing Phase 07 matrices from running in one pytest invocation. Real production workloads (long-running daemons serving many shell calls) hit the same leak — Phase 07 just made it visible.
- **Stager byte-copy** is the dominant cost above 64 KiB files (33 % of `commit_s` at 1 MiB, ~470 MB/s ceiling). One redundant byte read per stager write is on the trivial-removal list.
- **`read_symlink` and `list_dir`** still walk per-layer filesystem on every call. Lane D's `LayerIndex` already has the data — these two verbs were not migrated in Phase 2.

None of these three is architecturally novel. All have direct line-numbered Phase 2.5 evidence. Phase 3 lands them.

---

## 2 Out of scope

The following are explicitly NOT Phase 3 work (per Phase 2.5 §"Next: improvements" non-goals):

- Reviving `MaterializedSnapshotCache` (Phase 04.5 retired it; needs lease-aware eviction = separate phase).
- Multi-lane OCC commits for disjoint paths (concurrency model change).
- Pushing stager throughput past the ~470 MB/s host-fs ceiling (requires `sendfile()` exploration; not cheap).
- Persisting `LayerIndex` to disk for warm restart (rglob is already fast; persistence not load-bearing).
- The `mixed` workload's parallel_efficiency cliff at c=10 (0.56). This is investigation-only, scheduled here as item #4 *to investigate*, not to fix.

---

## 3 Improvements (the work)

### 3.1 Improvement #1 — Daemon `/dev/shm` cleanup

**File:** `backend/src/sandbox/daemon/services/shell_runner.py`, the `try`/`finally` block in `_handle_command_exec_request` (line ~77).

**Change:** Add `shutil.rmtree(run_dir, ignore_errors=True)` to the outer `finally`. The lease-release branch and the lowerdir-drop branch already exist; this is a third cleanup line.

**Estimated LOC:** ~3.

**Discriminator metric (must be true before merge):**
- Before: a benchmark that runs `tool.shell("true")` 200× in one daemon-process leaves `/dev/shm/eos-command-exec/<key>/` holding ≥ 200 run-dir entries.
- After: the same benchmark holds ≤ 5 run-dir entries at any sample point (only currently-active calls).

**Risk:** Trivial. `ignore_errors=True` makes failure non-fatal; cleanup runs after capture and commit are done.

### 3.2 Improvement #2 — Drop redundant byte-read in `changeset.py:31`

**File:** `backend/src/sandbox/command_exec/capture/changeset.py` (line 31), and `WriteChange` constructor in the OCC layer.

**Change:** Thread the precomputed `final_hash` from `OverlayPathChange` directly into `WriteChange`, skipping the `Path(content_path).read_bytes()` call that exists only to recompute the hash. The stager (`_LayerChangeStager.write`) already does its own byte read for the actual stage step — this second read at capture time is the redundancy.

**Estimated LOC:** ~10 across two files.

**Discriminator metric:**
- Before (Phase 2.5 size matrix, tracked, 1 MiB × 8 cell): `commit_s` = 0.054 s, `stager_s` = 0.018 s.
- After: `commit_s` ≤ 0.049 s (≥ 9 % drop, capturing the savings of one redundant 1-MiB read per file). Same `stager_s` (the stager itself is unchanged); the savings are in `capture_upperdir_s`.

**Risk:** Low. `final_hash` is already populated on `OverlayPathChange`; this is one assignment swap. Verify by re-running Phase 07 size matrix and checking `commit_s` at 1 MiB drops; check unit-test `test_changeset` still passes.

### 3.3 Improvement #3 — Migrate `read_symlink` and `list_dir` to `LayerIndex`

**File:** `backend/src/sandbox/layer_stack/merged_view.py`, methods `read_symlink` (line ~77) and `list_dir` (line ~94).

**Change:** Same pattern as Lane D applied to `read_bytes`:
1. Consult `self._layer_index(layer)` first.
2. For `read_symlink`: if `rel` not in `index.files` and not in `index.whiteouts` and no ancestor in `index.files` or `index.opaque_dirs`, return `("", False)` immediately.
3. For `list_dir`: build the merged child set from `index.files` + `index.whiteouts`, falling back to `iterdir()` only when the index says the directory has live children.

After both callers migrate, retire `_whiteout_path`, `_has_file_ancestor`, `_has_opaque_ancestor` (all callers gone).

**Estimated LOC:** ~30 added to `merged_view.py`, ~25 deleted. Net ≈ +5 LOC.

**Discriminator metric (Phase 1 c20 matrix re-run on Lane D + improvement #3):**
- Before (Phase 2.5 c-matrix snapshot): read_file c20 = 23.71 ops/s; edit_file c20 = 21.82 ops/s.
- After: read_file c20 ≥ 30 ops/s (≥ 26 % gain); edit_file c20 ≥ 28 ops/s (≥ 28 % gain). Floor: read_request_s p99 at c20 must improve, not regress, on every workload.

**Risk:** Low. Same correctness story as Lane D's `read_bytes` migration (immutable per-layer index, frozenset lookups). The retired helpers are pure (no side effects), so removal is mechanical.

### 3.4 Investigation #4 (no code change in this phase) — `mixed` workload c=10 cliff

Phase 2.5 c-matrix snapshot showed `mixed` parallel_efficiency drops to 0.56 at c=10 and 0.60 at c=20, while read/write/edit/shell stay above 0.83. The hypothesis is OCC commit-queue contention from interleaving heterogeneous per-call cost profiles.

**Deliverable:** A profiling artifact (no code change) at `.omc/results/phase08-mixed-contention-profile-<run_id>.txt` containing:
- Per-call timings broken down by op (read vs write vs edit vs shell) at c=10.
- OCC commit-queue wait time (`occ.commit.queue_wait_s` if instrumented; otherwise add it as a 5-LOC instrumentation).
- A 1-paragraph hypothesis statement: which class of contention is responsible.

If the profile reveals a one-instrumentation-pass-away fix, schedule it for Phase 3.5; otherwise document as Phase 4 candidate.

---

## 4 Verifications (the asserts)

These tighten correctness coverage. Phase 2.5 §"Next: verifications" table is the source.

### 4.1 Verification A — Byte-content asserts in Phase 07 cells

**File:** `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase07_complex_capture_metrics.py`.

**Change:** For each cell, after the file-count assert, read one path via `tool.read_file` and assert the content prefix matches the kind:
- size cells: prefix is `b'x' * (size − 16)` followed by `i={i:013d}\n`.
- new cells in kind matrix: prefix is `b'new i='`.
- modify cells: prefix is `b'modified i='`.
- mixed_kinds cells: assert one path each from the modify and new ranges.
- mixed_routing cells: prefix is `b'gated i='` for the gated dir, `b'dist  i='` for the dist dir.

**Estimated LOC:** ~30. One extra `tool.shell` call per cell (the `read_file` daemon-API path is fine).

### 4.2 Verification D — Lease-pinning unit test

**File:** new `backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_pinning.py`.

**Change:** A unit test (no live sandbox) that:
1. Builds a 5-layer manifest in a temp dir.
2. Acquires N=4 concurrent leases.
3. While all 4 are held, churns the active manifest by appending and removing leaf layers.
4. After each churn, verifies `evict_layer_index` was NEVER called for a layer-id still pinned by any lease.
5. After all leases release, verifies eviction happens exactly for layers no manifest still references.

**Estimated LOC:** ~60.

### 4.3 Verification E — `/dev/shm` cleanup regression test

**Depends on improvement #1 landing.**

**File:** new `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase08_dev_shm_bounded.py`.

**Change:** A live test that:
1. Loops `tool.shell("true")` 200 times sequentially.
2. Probes `/dev/shm/eos-command-exec/` via `raw_exec` after every 50 calls.
3. Asserts the number of run-dir entries stays ≤ 5 at every sample point.
4. Asserts total `du -s /dev/shm` size stays ≤ 5 MiB.

**Estimated LOC:** ~40.

### 4.4 Verification B (lower priority) — Manifest-state asserts

Phase 07 trusts `gated_path_count` / `direct_path_count` from commit timings. Verification B reads the active manifest's top layer and asserts the path set matches `(workload added) ∪ (workload modified) − (workload deleted)`.

**Defer to Phase 3.5** unless improvements #1–#3 reveal a manifest-state bug that asserts could have caught earlier. Adding it now duplicates the OCC routing-count assert without independent value.

### 4.5 Verification C (depends on #1) — Concurrency-axis run on Phase 07 matrices

Wrap each Phase 07 cell in `gather_with_barrier(concurrency=N)` for N ∈ {1, 5, 10, 20}. Skip cells whose total path-count × concurrency would exceed `/dev/shm` budget (post-#1 the budget is much larger but still finite).

**Defer to Phase 3.5** until #1 lands and the Phase 08 regression test confirms `/dev/shm` is bounded. Otherwise the matrix will hit the same ENOSPC Phase 07 hit.

---

## 4A Complex live e2e sandbox testing (the strict tier)

Phase 2.5's three matrices each isolate ONE axis. The user's mandate is "very complex and strict, careful live e2e sandbox testing", so Phase 3 adds a `test_phase09_*` suite that combines axes, exercises adversarial inputs, and runs long-soak stability checks. Every test produces a structured JSONL artifact with a fixed schema (§4D) so cross-run comparison is mechanical.

**Sandbox provider:** every test in this section dispatches against the real Daytona-provisioned sandbox via `live_sandbox` / `workspace_base_sandbox` fixtures (no mocks, no in-process daemon). Failures must surface real sandbox-side errors — silent partial-workload truncation (the kind that hid the bash K=10K bug for an entire phase) is a P0 regression and the test design must catch it.

### 4A.1 Multi-axis cross-product matrices

The three Phase 2.5 axes (size, kind, routing) plus concurrency multiplied together would be 4D. The product is too large to run exhaustively under the `/dev/shm` budget, so Phase 3 ships **three orthogonal 2D slices** that cover the high-leverage regions:

| Slice | Axes (held vs varied) | Cells | Purpose |
|---|---|---:|---|
| **9.1 size × kind** | hold prefix=tracked, k=64; vary file_size_bytes ∈ {64, 4 KiB, 64 KiB, 1 MiB} × kind ∈ {NEW, MODIFY, DELETE, MIXED} | 16 | Does Lane D's kind-agnosticism hold at 1 MiB? Does delete-of-1MiB cost the same as delete-of-64B (since stager_n = 0 either way)? |
| **9.2 size × concurrency** | hold prefix=tracked, kind=NEW, k=16; vary file_size_bytes ∈ {64, 4 KiB, 64 KiB, 1 MiB} × concurrency ∈ {1, 5, 10, 20} | 16 | At what file size does parallel_efficiency cliff? Stager bottleneck under contention? |
| **9.3 kind × concurrency** | hold prefix=tracked, file_size=64B, k=100; vary kind ∈ {NEW, MODIFY, DELETE, MIXED} × concurrency ∈ {1, 5, 10, 20} | 16 | Does MIXED's c=10 efficiency cliff (Phase 2.5 found 0.56) survive on a single workload class, or is it specific to interleaved heterogeneity? |

**Strict pass-fail bars** (must hold for every cell):

- `result.success == True` for the timed shell AND for every untimed setup shell.
- File-count assert (count of regular files under the cell dir matches the workload's intended post-state).
- **Byte-content assert** (per Verification A): one path read back, prefix matches the kind.
- **Manifest-state assert** (per Verification B): top-layer manifest path-set matches `(workload added) ∪ (workload modified) − (workload deleted)`.
- **Per-call wall_ms p99 ≤ 3 × p50** within each cell (no fat tails — fat tails almost always indicate a hidden retry or a partial-workload silent truncation).
- For concurrency cells: `parallel_efficiency ≥ 0.50`. If a cell drops below 0.50, the test fails with the cell ID and a contention-class hypothesis printed; the failure does not abort the matrix (other cells continue).

### 4A.2 Realistic-install workload

Synthetic K-files-of-uniform-size benchmarks miss the file-mix shape of real `pip install` / `npm install`: many small files, a few large files, deep directory nesting, occasional symlinks. Add **one cell** that mimics this shape:

- 950 files of 4 KiB each (mostly source).
- 30 files of 64 KiB each (mid-size).
- 15 files of 1 MiB each (compiled artefacts).
- 5 files of 4 MiB each (vendor binaries).
- 50 symlinks (10 % of source paths).
- Directory depth: 8 levels (mimicking `node_modules/<pkg>/<sub>/<mod>/<file>`).

**Pass bars:** `commit_s ≤ 2.0 s`, total bytes captured ≥ 30 MiB, `actual_files == 1000` regular + `actual_symlinks == 50`. This cell also exercises Verification A's byte-content prefix on a symlink target.

### 4A.3 Soak / stability test (depends on improvement #1)

A long-running shell-loop that exists specifically to catch resource leaks:

- 500 sequential `tool.shell` calls, each creating 100 NEW files of 4 KiB.
- Probe `/dev/shm` after every 50 calls via `raw_exec`: total bytes, run-dir count, inode count.
- Probe daemon RSS via `/proc/$pid/status` after every 100 calls.
- Probe `_layer_index_cache` size by reading the daemon-API `api.layer_metrics` extension (add this if missing).

**Strict pass bars:**

- `/dev/shm` run-dir count stays ≤ 5 throughout (the Verification E threshold).
- `/dev/shm` total size stays ≤ 5 MiB throughout.
- Daemon RSS growth from call 100 to call 500 ≤ 100 MiB (the cache pins active layers; growth should plateau, not climb linearly).
- `_layer_index_cache` entry count stays ≤ (active manifest depth + 5) — i.e. bounded by what's actually pinned.
- No call exceeds `wall_ms p99 × 3` measured at call 50 (catches degradation creep).

If any soak bar misses, the failure record includes the exact call-number + sample where the breach happened.

### 4A.4 Adversarial / edge-case cells

These are not perf benchmarks — they exist to catch correctness regressions Lane D and the python driver could plausibly miss. Each cell is one shell call, asserting outcome.

| Adversarial scenario | What it tests |
|---|---|
| **Deeply nested path** (depth=20, total path length ~500 chars) | `LayerIndex` keys cross hash buckets; `os.walk` cost; `_join_rel` correctness on long paths. |
| **Symlink target = absolute path inside workspace** | Capture pipeline correctly stores the symlink target as a string (no follow). |
| **Symlink target = absolute path OUTSIDE workspace** | Daemon rejects or correctly records (workspace boundary check). |
| **Whiteout collision**: workload deletes file X, then in same commit creates file X | OCC commit produces ONE entry (a write), not a delete+write pair. |
| **Same-name file in two different layers** | Lane D's `_layer_index_cache` returns the YOUNGER layer's content (manifest order). |
| **Path with embedded `\n` or special bash chars** | Heredoc python driver correctly quotes via `repr()` — no shell injection. |
| **File with name = whiteout marker** (`.wh.foo`) | Daemon disambiguates real-file vs whiteout. |
| **File with name = opaque marker** (`.wh..wh..opq`) | Daemon disambiguates real-file vs opaque-dir marker. |
| **Empty directory commit** | OCC produces 0 path changes; commit_s ≈ 0. |
| **Filename with 255-char limit** | Filesystem max-name boundary; capture & stage both succeed. |

Each adversarial cell has a single explicit assertion: the post-commit state contains exactly the path(s) the workload intended, with the bytes the workload wrote. Fail with the cell ID.

### 4A.5 Cache-correctness lease-churn live test

Verification D (§4.2) is a unit test. This is its live counterpart, exercising the real daemon:

- 5 concurrent shell calls, each on a DIFFERENT historical manifest version (achieved by interleaving commits on a third worker).
- Each call holds its lease for 2 seconds while reading 100 paths via `tool.read_file`.
- During those 2 seconds, the third worker performs N=10 commits that add and remove leaf layers.
- Assert: every read succeeds; daemon log shows zero `evict_layer_index` calls for any layer-id still pinned by any of the 5 leases.
- Assert: post-test, `_layer_index_cache` size shrinks back to (final-active-manifest-depth) once all leases release.

### 4A.6 Failure-injection cells

These deliberately push the daemon into an error path and assert the failure mode is *clean* — no partial commit, no orphaned upper-dir, no stuck leases.

| Injection | Expected daemon behavior |
|---|---|
| Workload writes 64 MiB (exceeds `/dev/shm`) | Commit fails with `ENOSPC` surfaced as `_DaemonDispatchError`; lease released; layer-stack manifest unchanged; subsequent shell calls succeed. |
| Workload exits with `kill -9 $$` mid-write | Daemon captures whatever is on upperdir, commit succeeds with partial paths; OCC manifest reflects only what was actually written. |
| Workload writes a file whose path normalizes to outside `/testbed` (e.g. `../../../etc/foo`) | Daemon rejects at validate-stage; commit fails; no lease leak. |
| Daemon kill-9 between capture and OCC commit (need test infra) | Subsequent restart finds NO orphan layer in storage; lease registry is clean. |

The kill-9 mid-pipeline cell may require Phase 4 daemon restart-recovery instrumentation. Treat as stretch goal; if not implementable in Phase 3, document and defer.

---

## 4B Test deliverables (concrete files)

| File | What lands |
|---|---|
| `backend/tests/live_e2e_test/sandbox/_harness/large_capture_workload.py` | Add `build_install_shape_workload()` for 4A.2; add `build_adversarial_*` builders for 4A.4; reuse `build_sized_capture` / `build_modify_capture` etc. for 4A.1–4A.3. |
| `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase09_complex_e2e.py` | All cells from 4A.1–4A.6. |
| `backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_pinning.py` | Verification D (§4.2). |
| `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase08_dev_shm_bounded.py` | Verification E + Soak (§4A.3). |
| `backend/src/sandbox/daemon/handlers/workspace.py` | Extend `api.layer_metrics` to return `_layer_index_cache_size` if missing — needed by the Soak test. |

---

## 4C Strict pass-fail discipline

Every test in §4A uses **assertion-style pass bars**, not "log and continue". Specifically:

- A cell fails the moment any assertion misses; the matrix continues so one bad cell doesn't hide downstream regressions.
- The artifact JSONL row for a failed cell carries `passed: false` and a structured `failure_reason` with category ∈ {`success_check`, `count_mismatch`, `content_mismatch`, `manifest_mismatch`, `latency_p99`, `parallel_efficiency`, `resource_leak`, `injection_unexpected_path`}.
- The summary row at end-of-matrix asserts `failed_cells == 0` and prints all failed cell IDs.
- A pre-existing cell that previously passed flipping to fail is a **mandatory advisor checkpoint** — never paper over with `pytest.xfail` or by tightening the threshold.

---

## 4D Detailed evaluation-metrics schema

Every Phase 3 live-e2e cell emits a JSONL row with the following schema. Schema name `phase09.live_e2e.v1` distinguishes it from Phase 06/07.

```json
{
  "schema": "phase09.live_e2e.v1",
  "matrix": "size_x_kind | size_x_concurrency | kind_x_concurrency | install_shape | soak | adversarial | lease_churn | injection",
  "cell_id": "<unique per cell>",
  "axis_values": { "file_size_bytes": 65536, "k": 64, "kind": "modify_files",
                   "concurrency": 10, "prefix": "tracked", "k_gated": 0, "k_dist": 0,
                   "adversarial_kind": null, "injection_kind": null },
  "passed": true,
  "failure_reason": null,
  "wall_ms": { "p50": 234.5, "p90": 256.1, "p99": 289.3, "max": 312.0 },
  "occ_timings": {
    "commit_s": 0.107,
    "validate_groups_s": 0.043,
    "publish_layer_s": 0.052,
    "stager_write_total_s": 0.024,
    "stager_write_count": 64,
    "gated_path_count": 64, "direct_path_count": 0,
    "gated_read_current_total_s": 0.011,
    "gated_apply_changes_total_s": 0.001,
    "gated_stage_delta_total_s": 0.024,
    "occ_prepare_groups_s": 0.001,
    "occ_group_by_route_s": 0.014
  },
  "capture_timings": {
    "capture_upperdir_s": 0.033,
    "overlay_capture_walk_upperdir_s": 0.011
  },
  "concurrency_metrics": {
    "parallel_efficiency": 0.86,
    "parallel_factor": 0.86,
    "calls": 10,
    "batch_wall_ms": 1166.7
  },
  "correctness": {
    "expected_files": 64,
    "actual_files": 64,
    "content_prefix_check": true,
    "manifest_path_set_check": true,
    "expected_gated": 0, "actual_gated": 0,
    "expected_direct": 0, "actual_direct": 0
  },
  "resource_snapshot": {
    "dev_shm_total_bytes": 102400,
    "dev_shm_run_dir_count": 1,
    "daemon_rss_kb": 28452,
    "layer_index_cache_size": 4
  },
  "derived": {
    "commit_per_file_us": 1672.0,
    "stager_per_file_us": 375.0,
    "throughput_ops_s": 8.6
  },
  "run_id": "20260508T123456Z-12345"
}
```

**Required summary row** at the end of every matrix (one extra JSONL line):

```json
{
  "schema": "phase09.live_e2e.summary.v1",
  "matrix": "size_x_kind",
  "run_id": "20260508T123456Z-12345",
  "total_cells": 16,
  "passed_cells": 16,
  "failed_cells": 0,
  "failed_cell_ids": [],
  "elapsed_total_s": 32.4,
  "artifact": ".omc/results/phase09-size-x-kind-...jsonl"
}
```

The summary row is the **single source of truth** for whether a matrix passed. CI gates on `failed_cells == 0` only.

### 4D.1 Cross-run comparison contract

Every artifact is filename-stable (same matrix → same filename pattern → trivially diff-able across runs):

- `.omc/results/phase09-size-x-kind-{run_id}.jsonl`
- `.omc/results/phase09-size-x-concurrency-{run_id}.jsonl`
- `.omc/results/phase09-kind-x-concurrency-{run_id}.jsonl`
- `.omc/results/phase09-install-shape-{run_id}.jsonl`
- `.omc/results/phase09-soak-{run_id}.jsonl`
- `.omc/results/phase09-adversarial-{run_id}.jsonl`
- `.omc/results/phase09-lease-churn-{run_id}.jsonl`
- `.omc/results/phase09-injection-{run_id}.jsonl`

A small companion script (`backend/tests/live_e2e_test/_tools/compare_phase09.py`, ~80 LOC) consumes two run_ids and produces a diff table flagging cells where p99 wall_ms regressed > 10 % or correctness flipped. The Phase 3 closing report runs this script before/after the implementation block.

---

## 5 Sequencing & dependencies

```
#1 (daemon shm cleanup) ──┬──> Verification E (post-#1 regression test)
                          ├──> Verification C (concurrency-axis Phase 07 matrices)
                          └──> §4A.3 Soak / stability test
                          └──> §4A.1 size×concurrency cells
                          └──> §4A.1 kind×concurrency cells

#2 (changeset hash threading) ── independent ── re-run Phase 07 size matrix
                                                + re-run §4A.1 size×kind matrix

#3 (read_symlink/list_dir migration) ── independent ── re-run Phase 1 c20 matrix
                                                       + re-run §4A.1 kind×concurrency matrix

Verification A (byte-content asserts) ── independent of #1/#2/#3 ── adds to test_phase07
                                                                     + reused in test_phase09

Verification D (lease-pinning unit) ── independent ── pure unit test

§4A.2 install-shape       ── independent (small workload) ── runs anytime
§4A.4 adversarial          ── independent ── pure correctness, no perf gating
§4A.5 lease-churn live    ── independent of #1/#2/#3 ── live counterpart of D
§4A.6 failure injection    ── independent (#1 makes it cleaner)
```

**Recommended order of execution:**

1. **Correctness floor** (no perf dependency): Verification A, Verification D, §4A.4 adversarial cells, §4A.5 lease-churn live test. All can land in parallel.
2. **Improvement #1** — daemon `/dev/shm` cleanup. Unblocks every concurrency / soak / batched test below.
3. **Verification E** + **§4A.3 soak**. Both regress #1; both must pass before any further code change.
4. **§4A.2 install-shape** cell. Single-cell baseline of "realistic install" perf — captures the pre-#2/#3 number for cross-run comparison.
5. **Improvement #2 + Improvement #3** in parallel.
6. **Re-runs**: Phase 07 size matrix (#2 falsifier), Phase 1 c20 matrix (#3 falsifier), §4A.1 size×kind (#2 falsifier on combined axis), §4A.1 kind×concurrency (#3 falsifier on combined axis), §4A.1 size×concurrency (joint #2+#3 effect), §4A.2 install-shape (joint effect on realistic workload).
7. **§4A.6 failure injection** cells. Cleanest failure semantics arrive after #1 lands.
8. **Investigation #4** profile artifact (using the §4A.1 kind×concurrency artifact as input).
9. **Cross-run diff** via `compare_phase09.py` (§4D.1) — produces the Phase 3 closing-report perf table.

---

## 6 Acceptance criteria (falsifiable)

This phase is "done" only when ALL criteria below pass on a fresh run. Any miss is a triage gate.

### 6A Improvements + base verifications

| # | Criterion | Threshold | Source artifact |
|---|---|---|---|
| 6.1 | Daemon `/dev/shm` run-dirs stay bounded under load | ≤ 5 dirs after 200 sequential shell calls | Verification E artifact |
| 6.2 | Phase 07 size matrix at 1 MiB × 8 (tracked) | `commit_s` ≤ 0.049 s | re-run `phase07-size-matrix-*.jsonl` |
| 6.3 | Phase 1 c20 matrix on Lane D + #3, read_file | ≥ 30 ops/s | re-run `live-e2e-phase05-public-file-ops-load_matrix-*.jsonl` |
| 6.4 | Phase 1 c20 matrix on Lane D + #3, edit_file | ≥ 28 ops/s | same |
| 6.5 | Phase 1 c20 matrix on Lane D + #3, no workload regresses | each c20 ops/s ≥ Phase 2.5 baseline | same |
| 6.6 | Phase 07 cells assert content prefix per kind | every cell has a `assert_content_prefix(...)` line | code review |
| 6.7 | Lease-pinning unit test passes | green on `pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_pinning.py` | unit-test output |
| 6.8 | `/dev/shm` regression test passes | green on `pytest backend/tests/live_e2e_test/.../test_phase08_dev_shm_bounded.py` | live-test output |
| 6.9 | All Phase 2.5 §"System-design invariants" still hold | manual code review against the 6 invariants | reviewer signoff |

### 6B Phase 09 complex live-e2e bars (the strict tier)

Every Phase 09 matrix's summary row must report `failed_cells == 0`. Specific cell-level bars:

| # | Criterion | Threshold | Source artifact |
|---|---|---|---|
| 6.10 | §4A.1 size×kind matrix — every cell passes | `failed_cells == 0`; every cell has correctness asserts (count + content + manifest) green | `phase09-size-x-kind-*.jsonl` summary row |
| 6.11 | §4A.1 size×concurrency matrix — every cell passes; parallel_efficiency ≥ 0.50 at every (size, c) cell except 1 MiB × c=20 (byte-bound by design) | `failed_cells == 0`; explicit per-cell `parallel_efficiency >= 0.50` (or documented exemption) | `phase09-size-x-concurrency-*.jsonl` summary row |
| 6.12 | §4A.1 kind×concurrency matrix — DELETE c=20 fastest, NEW/MODIFY/MIXED within 30 % of each other at c=20 | `failed_cells == 0`; ratio bound documented | `phase09-kind-x-concurrency-*.jsonl` |
| 6.13 | §4A.2 install-shape — `commit_s ≤ 2.0 s`, `actual_files == 1000`, `actual_symlinks == 50` | strict equality on counts | `phase09-install-shape-*.jsonl` |
| 6.14 | §4A.3 soak — all 4 strict bars hold (run-dir count ≤ 5, /dev/shm ≤ 5 MiB, RSS growth ≤ 100 MiB, p99 wall_ms degradation ≤ 3×) | every probe sample passes | `phase09-soak-*.jsonl` |
| 6.15 | §4A.4 adversarial — every adversarial cell passes its single explicit assertion | `failed_cells == 0` | `phase09-adversarial-*.jsonl` |
| 6.16 | §4A.5 lease-churn live — zero `evict_layer_index` calls for any pinned layer-id; cache shrinks to active-manifest depth post-test | strict equality | `phase09-lease-churn-*.jsonl` + daemon log |
| 6.17 | §4A.6 failure-injection — every injection produces a clean failure (no orphan layer, no stuck lease, subsequent shell succeeds) | strict; injection_unexpected_path is a hard failure | `phase09-injection-*.jsonl` |
| 6.18 | Cross-run diff via `compare_phase09.py` shows no cell regressed > 10 % on `wall_ms p99` between Phase 2.5 baseline and Phase 3 final | diff script exits 0 with no flagged cells | run script output |

**Triage checkpoints (advisor consult mandatory):**

- 6.2 miss → hash-threading rework needed.
- 6.3 / 6.4 miss → `read_symlink` / `list_dir` cost profile differs from `read_bytes`.
- 6.10–6.12 miss → multi-axis interaction we didn't predict; do NOT proceed to release.
- 6.14 miss → leak that #1 didn't catch; root-cause before any further perf claim.
- 6.16 / 6.17 miss → correctness regression; STOP the phase, escalate.

---

## 7 Advisor checkpoint

Mandatory advisor consult **before** starting work on improvement #2, with the following question:

> Phase 2.5 measured stager_s = 0.018 s at 1 MiB × 8 (tracked), commit_s = 0.054 s. The redundant `Path(content_path).read_bytes()` in `changeset.py:31` is the largest non-stager byte traffic in capture. What's the predicted floor for `commit_s` at 1 MiB × 8 after threading `final_hash` through, and how should we verify the prediction before declaring #2 done?

The point is to set the falsifier *before* writing the code.

A second mandatory checkpoint **after** investigation #4's profiling artifact lands, with the question:

> The `mixed` workload's parallel_efficiency cliff at c=10 (0.56) is documented but not yet diagnosed. Profile artifact attached. Is the contention class identified by the profile (a) one-instrumentation-pass-away from a fix, (b) requires a Phase 4 architectural change, or (c) acceptable as documented behaviour?

---

## 8 Things to keep stable (don't touch in this phase)

These were load-bearing in Phase 2.5; modifying them would invalidate the metrics corpus:

- `LayerIndex` dataclass shape (`files`, `whiteouts`, `opaque_dirs` as `frozenset[str]`).
- `_layer_index_cache` as a dict on `MergedView`, keyed by `layer_id`.
- `_remove_unreferenced_layers` as the sole call site for `evict_layer_index`.
- `_py_driver` heredoc shape in `large_capture_workload.py` (changing it would break artifact comparability).
- The 6 invariants in Phase 2.5 §"System-design invariants to preserve".

---

## 9 Estimated total effort

### 9A Improvements + base verifications (Phase 3 core)

| Block | LOC | Estimated time |
|---|---:|---|
| Improvement #1 (shm cleanup) | ~3 | 30 min code + 30 min Verification E test = 1 h |
| Improvement #2 (hash threading) | ~10 | 1 h code + 1 h benchmark = 2 h |
| Improvement #3 (symlink/list_dir) | ~5 net | 2 h code + 2 h benchmark = 4 h |
| Verification A (byte-content) | ~30 | 1 h |
| Verification D (lease-pinning unit test) | ~60 | 2 h |
| Verification E (shm regression test) | ~40 | 1 h |
| Investigation #4 (profile artifact) | ~5 (instrumentation) | 2 h profiling + 1 h writing = 3 h |
| Re-run Phase 06/07 benchmarks + report writing | n/a | 2 h |

**Subtotal core:** ~16 h, ~+160 LOC (production +25, tests +135).

### 9B Phase 09 strict live-e2e tier

| Block | LOC | Estimated time |
|---|---:|---|
| §4A.1 size×kind matrix (16 cells) | ~80 (test bodies + builders) | 2 h code + 1 h run/triage = 3 h |
| §4A.1 size×concurrency matrix (16 cells) | ~70 (reuse phase05 c-matrix harness) | 2 h |
| §4A.1 kind×concurrency matrix (16 cells) | ~60 | 1.5 h |
| §4A.2 install-shape workload + builder | ~40 builder + ~30 test = ~70 | 2 h |
| §4A.3 soak / stability + `api.layer_metrics` extension | ~40 test + ~10 daemon = ~50 | 2 h |
| §4A.4 adversarial cells (~10 cells) | ~120 (one builder per cell + asserts) | 4 h |
| §4A.5 lease-churn live test | ~80 | 2 h |
| §4A.6 failure-injection cells (skip kill-9 mid-pipeline if not implementable) | ~80 | 3 h |
| `compare_phase09.py` cross-run diff script | ~80 | 1.5 h |
| Phase 09 run + summary / triage | n/a | 3 h |

**Subtotal Phase 09:** ~24 h, ~+660 LOC (test-only) + ~10 LOC daemon (`api.layer_metrics` field) + ~80 LOC tooling.

### 9C Combined estimate

**Total Phase 3 estimate:** ~40 h focused work, ~5–8 sessions if interleaved with advisor checkpoints (§7) and review.

**Net code growth:** ≈ +830 LOC. Of which:
- Production code: **~35 LOC** (#1: 3, #2: 10, #3: 5 net, daemon api: 10, plus a few wiring lines).
- Test code: **~715 LOC** (verifications A/D/E plus all of §4A.1–4A.6).
- Tooling: **~80 LOC** (`compare_phase09.py`).

The "system stays small" invariant from Phase 2.5 §"System-design invariants to preserve" holds: production code grows by ~35 LOC for ~10× more test coverage. Phase 3 is overwhelmingly a *verification investment* on top of three small production tweaks — not a feature build.

---

## 10 Stop conditions

Stop the phase and report to the user if:

- Improvement #1 introduces a regression on any existing live test (the cleanup happens after release, but if e.g. the run-dir is referenced post-cleanup we'd see it).
- Improvement #2 misses 6.2 by more than a small margin (the predicted floor was wrong → re-plan).
- Improvement #3 regresses any c20 workload (Lane D's read_bytes parity didn't carry over to symlink/dir-listing → re-plan).
- Verification D reveals a real lease-pinning bug — escalate before any further code change.
- Investigation #4's profile reveals a structural OCC contention issue requiring Phase 4 architectural work — pause Phase 3, write the Phase 4 plan first.
- **Phase 09 §4A.4 adversarial cell exposes a correctness bug** (e.g., whiteout-collision produces both delete + write entries, or symlink-target encoding round-trips wrong) — STOP the phase, escalate, fix the production bug before any further test work.
- **Phase 09 §4A.5 lease-churn live test logs an `evict_layer_index` call for any pinned layer-id** — STOP. This is the hard-block correctness invariant from Phase 2.5 §"System-design invariants" #2.
- **Phase 09 §4A.6 failure-injection cell produces an orphan layer or stuck lease** — STOP. The daemon's failure semantics are broken; investigate before any further code change.
- **Phase 09 §4A.3 soak shows linear RSS growth** (rather than plateau) — STOP. Cache eviction is broken; root-cause before declaring #1 done.
- **Phase 09 cross-run diff (§4D.1) flags any cell as > 10 % p99 wall_ms regression vs Phase 2.5 baseline** — advisor checkpoint mandatory before merge.
- **Any Phase 09 cell fails its strict pass bars and the failure mode is "we need to relax the threshold"** — that's the polite-stop anti-pattern. NEVER relax a pass bar; root-cause first.
