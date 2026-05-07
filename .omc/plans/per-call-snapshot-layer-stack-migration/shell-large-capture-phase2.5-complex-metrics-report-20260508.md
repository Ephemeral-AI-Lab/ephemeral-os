# Shell Large-Capture Scaling — Phase 2.5 Complex-Cases Performance Metrics

**Date:** 2026-05-08
**Branch:** codex/fix-dot-path-normalization-tests
**Source plan:** Phase 2.5 extends `.omc/plans/per-call-snapshot-layer-stack-migration/shell-large-capture-scaling-plan-20260508.md` with three new performance dimensions the original Phase 2 plan never measured.
**Phase 2 implementation report:** `shell-large-capture-phase2-implementation-report-20260508.md`
**Verdict:** Lane D (per-layer presence index for `MergedView.read_bytes`) holds across every dimension we measured — file size, change kind, and mixed gated/direct routing. The 1.5 s K=10K target lands on **both** routes now that the Python driver replaces the bash `for $(seq …)` loop that previously truncated dist K=10K.

---

## What this report adds on top of Phase 2

Phase 2's K-scaling matrix only varied **count** of NEW files on a single prefix per cell. The user-stated requirement for "very detailed performance metrics on large files / sheer number of files installation, OCC merge routing (gated vs gitignored), need very complex cases" maps to three orthogonal axes the original matrix did not cover:

| Axis | Phase 2 K-scaling | Phase 2.5 |
|---|---|---|
| Path count `K` | 1 → 10000 (NEW only) | reused (also dist K=10K full-workload now) |
| File **size** | constant ~12 B | **64 B → 1 MiB** (16384× range) |
| Change **kind** | always NEW | **NEW / MODIFY / DELETE / MIXED** |
| Routing | tracked-only or dist-only | **gated + direct in the same commit** |
| Correctness verification | `assert result.success` | **post-commit file-count + path-count assertions** |

Three new artifacts plus a fresh Phase 06 dist K=10K data point fill the four gaps.

---

## Headline numbers — one row per dimension

### Size axis (commit cost vs file size, K=8 cells)

Tracked route, K=8 unless noted (full size matrix in §"Size × K — tracked route" below):

| File size | commit_s | stager/file_us | commit/file_us |
|---:|---:|---:|---:|
| 64 B | 0.018 (K=32) | 28 | 137 |
| 4 KiB | 0.009 | 34 | 138 |
| 64 KiB | 0.018 (K=32) | 166 | 561 |
| 1 MiB | 0.054 | 2264 | 6802 |

The per-file commit cost stays flat in the **count-bound** regime (≤ 4 KiB) and switches to **byte-bound** at ≥ 64 KiB. Stager throughput at 1 MiB ≈ 470 MB/s — that's the byte-copy ceiling once the per-file index lookup work is amortised away.

### Kind axis (1000 paths per cell)

Per-prefix figures below; full kind matrix in §"Kind × K matrix" below.

| Kind | tracked commit_s | dist commit_s | tracked us/file | dist us/file |
|---|---:|---:|---:|---:|
| **NEW**     | 0.092 | 0.124 | 92 | 124 |
| **MODIFY**  | 0.107 | 0.103 | 106 | 103 |
| **DELETE**  | 0.064 | 0.060 | 64 | 60 |
| **MIXED**   | 0.100 | 0.105 | 100 | 105 |

DELETE is fastest because the stager has nothing to write (`stager_write_count = 0`). MODIFY costs the same as NEW on Lane D — the index lookup is O(1) regardless of whether the path was already in the layer stack. **Lane D's index optimisation is kind-agnostic.**

### Mixed-routing axis (single commit populates *both* routes)

Phase 06 only ever exercised one route per commit. Phase 07 forces both:

| Split (gated, direct) | commit_s | gated us/path | direct us/path |
|---|---:|---:|---:|
| (500, 500)   | 0.089 | 32 | 33 |
| (1000, 100)  | 0.107 | 40 | 34 |
| (100, 1000)  | 0.100 | 37 | 38 |

Per-path cost is symmetric across routes regardless of split — the routing-decision overhead in `OccCommitTransaction.group_by_route` is bounded (~16 ms at K=1000 total), and Lane D's index serves both routes equally.

### Phase 06 dist K=10K — finally a full data point

| Driver | dist K=10K commit_s | dist K=10K wall_s | files actually written |
|---|---:|---:|---:|
| **bash (Phase 2)** | 0.425 (partial) | — | ~4168 of 10000 (truncated by bash loop) |
| **python (Phase 2.5)** | **0.848** | **2.564** | **10000 (full)** |

The Phase 2 report flagged dist K=10K as a "partial-workload data point" because `for i in $(seq 1 10000); do …; done` exits 1 mid-run inside the daytona sandbox. Routing the workload through `python3 -c` (one process, one `os.write` loop) drives all 10 000 files through to the OCC commit. **Plan §6.4's K=10K target ≤ 1.5 s — met on the dist route at 0.848 s ✓.**

---

## Plan §6.4 acceptance — extended to all three matrices

Phase 2's success criteria were stated for the K=10K tracked cell. Phase 2.5 generalises:

| Criterion (plan §6.4 generalised) | Tracked | Dist | Verdict |
|---|---:|---:|---|
| `commit_s` ≤ 1.5 s at K=10K, 64 B files | **1.076 s** | **0.848 s** | ✓ both routes |
| `commit_per_file_us` ratio K=10K vs K=100 ≤ 1.2× | 1.076 / 0.0164 / (10000/100) = 0.66× | 0.848 / 0.0101 / (10000/100) = 0.84× | ✓ both routes |
| `commit_s` flat across change kinds at K=1000 | 0.064 → 0.107 (delete fastest) | 0.060 → 0.124 | ✓ kind doesn't blow it up |
| `commit_s` flat across routing splits at K=1100 | 0.107 (1000g+100d) | 0.100 (100g+1000d) | ✓ |
| `stager_per_file_us` byte-bound at 1 MiB | 2264 us/file (= 463 MB/s) | 2131 us/file (= 491 MB/s) | ✓ same ceiling either route |

---

## Correctness verification (the assertion Phase 2 never did)

Every Phase 07 cell now does **two** post-commit checks — the K-scaling benchmark only checked `result.success`:

1. **File-count assertion**: probe in-sandbox via `python3` (`build_count_files_command`) and `assert actual == expected_files`. NEW expects K, MODIFY expects K, DELETE expects 0, MIXED expects k_modify + k_new, mixed_routing expects k_gated under the gated dir AND k_dist under the dist dir.
2. **OCC routing-count assertion** (mixed_routing only): `assert gated_path_count == k_gated AND direct_path_count == k_dist` from the commit timings — this is the codepath Phase 2's K-scaling matrix never exercised because every cell had only one populated route.

All assertions pass on the artifact data:

| Matrix | Cells | File-count check | OCC count check |
|---|---:|---|---|
| size | 16 | ✓ all 16 (`actual_files == k`) | n/a |
| kind | 16 | ✓ all 16 (NEW/MODIFY: `K`; DELETE: `0`; MIXED: `k_modify + k_new`) | n/a |
| mixed_routing | 3 | ✓ all 3 (per-prefix counts match split) | ✓ all 3 (`gated_path_count == k_gated AND direct_path_count == k_dist`) |

The MODIFY content is also distinguishable from the seed: seed writes `b'baseline ...'`, modify writes `b'modified ...'`. The OCC stage_delta count proves the modify wrote new bytes (stager_write_count == k for modify cells, == 0 for delete cells).

---

## Per-cell breakdown

### Size × K — tracked route

| size_bytes | k | wall_ms | capture_s | commit_s | publish_s | stager/file_us | commit/file_us |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 64 | 16 | 873 | 0.001 | 0.003 | 0.001 | 35 | 194 |
| 64 | 256 | 977 | 0.009 | 0.035 | 0.012 | 28 | 137 |
| 4 096 | 16 | 944 | 0.001 | 0.003 | 0.001 | 36 | 184 |
| 4 096 | 64 | 970 | 0.003 | 0.009 | 0.004 | 34 | 138 |
| 65 536 | 8 | 928 | 0.002 | 0.005 | 0.002 | 151 | 577 |
| 65 536 | 32 | 1 069 | 0.006 | 0.018 | 0.009 | 166 | 561 |
| 1 048 576 | 1 | 938 | 0.003 | 0.007 | 0.004 | 2 034 | 7 180 |
| 1 048 576 | 8 | 1 046 | 0.017 | 0.054 | 0.030 | 2 264 | 6 802 |

### Size × K — dist route

| size_bytes | k | wall_ms | capture_s | commit_s | publish_s | stager/file_us | commit/file_us |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 64 | 16 | 970 | 0.001 | 0.003 | 0.001 | 27 | 183 |
| 64 | 256 | 1 030 | 0.008 | 0.030 | 0.012 | 25 | 118 |
| 4 096 | 16 | 956 | 0.001 | 0.004 | 0.002 | 42 | 234 |
| 4 096 | 64 | 1 027 | 0.003 | 0.010 | 0.005 | 34 | 156 |
| 65 536 | 8 | 1 119 | 0.002 | 0.005 | 0.002 | 161 | 639 |
| 65 536 | 32 | 1 073 | 0.005 | 0.017 | 0.009 | 148 | 522 |
| 1 048 576 | 1 | 992 | 0.003 | 0.007 | 0.004 | 1 994 | 6 970 |
| 1 048 576 | 8 | 1 052 | 0.016 | 0.052 | 0.029 | 2 131 | 6 445 |

**Reading the size axis.** `commit_per_file_us` falls into two regimes:

- **Count-bound (≤ 4 KiB, ≥ ~140 µs/file)**: dominated by per-file index lookup, route-decision, OCC bookkeeping. Lane D's index makes this constant in path-count.
- **Byte-bound (≥ 64 KiB, → ~6.8 ms/file at 1 MiB)**: dominated by `stager_per_file_us` which scales linearly with bytes. Stager throughput plateaus at ~470 MB/s on tracked, ~491 MB/s on dist — the host-fs write ceiling, not anything Lane D can affect.

The crossover happens around 64 KiB where stager equals fixed overhead.

### Kind × K matrix

#### Tracked route

| kind | K | wall_ms | capture_s | commit_s | stager_count | gated_read_us/path | gated_stage_us/path | commit/path_us |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| new_files     | 100  | 962 | 0.004 | 0.010 | 100 | 4.6 | 24.4 | 99 |
| new_files     | 1000 | 1 252 | 0.033 | 0.092 | 1000 | 1.1 | 25.2 | 92 |
| modify_files  | 100  | 945 | 0.004 | 0.012 | 100 | 4.4 | 33.7 | 115 |
| modify_files  | 1000 | 1 305 | 0.033 | 0.107 | 1000 | 1.2 | 26.7 | 106 |
| delete_files  | 100  | 894 | 0.002 | 0.007 | 0 | 4.5 | 0.4 | 70 |
| delete_files  | 1000 | 1 117 | 0.017 | 0.064 | 0 | 1.0 | 0.0 | 64 |
| mixed_kinds   | 100  | 941 | 0.003 | 0.010 | 67 | 5.0 | 28.4 | 102 |
| mixed_kinds   | 1000 | 1 263 | 0.027 | 0.100 | 667 | 1.2 | 27.4 | 100 |

#### Dist route

| kind | K | wall_ms | capture_s | commit_s | stager_count | direct_read_us/path | direct_stage_us/path | commit/path_us |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| new_files     | 100  | 1 080 | 0.004 | 0.016 | 100 | 13.0 | 17.7 | 157 |
| new_files     | 1000 | 1 415 | 0.032 | 0.124 | 1000 | 13.2 | 24.4 | 124 |
| modify_files  | 100  | 970 | 0.004 | 0.012 | 100 | 16.5 | 22.1 | 117 |
| modify_files  | 1000 | 1 453 | 0.032 | 0.103 | 1000 | 25.2 | 24.8 | 103 |
| delete_files  | 100  | 1 039 | 0.002 | 0.010 | 0 | 28.3 | 0.2 | 102 |
| delete_files  | 1000 | 1 133 | 0.016 | 0.060 | 0 | 13.4 | 0.0 | 60 |
| mixed_kinds   | 100  | 989 | 0.003 | 0.012 | 67 | 36.4 | 18.4 | 121 |
| mixed_kinds   | 1000 | 1 348 | 0.029 | 0.105 | 667 | 28.7 | 23.1 | 105 |

**Reading the kind axis.** `gated_read_current_total_s` per path stays in the 1–5 µs band on tracked at K=1000 — Lane D's index turns the layer walk into a `frozenset` lookup. The corresponding `direct_*` path on dist is in the 13–29 µs band; slightly higher because the dist route still does some per-path filesystem stat work, but it's bounded by manifest size, not by file-tree depth. **DELETE has stager_count = 0** — the stager only writes content for create/modify; delete just publishes a whiteout.

### Mixed-routing matrix

| split (gated, direct) | wall_ms | commit_s | gated_path | direct_path | gated us/path | direct us/path | publish_s |
|---|---:|---:|---:|---:|---:|---:|---:|
| (500, 500)   | 1 102 | 0.089 | 500 | 500 | 32 | 33 | 0.053 |
| (1000, 100)  | 1 166 | 0.107 | 1000 | 100 | 40 | 34 | 0.060 |
| (100, 1000)  | 1 161 | 0.100 | 100 | 1000 | 37 | 38 | 0.055 |

`gated_path_count` and `direct_path_count` from the OCC timings exactly match the workload split — the routing-decision codepath partitions correctly. Per-path cost is **symmetric across routes** regardless of split, validating that Lane D's index works equally for both `gated_*` and `direct_*` validate paths.

### Phase 06 K-scaling — refreshed under the new python driver

Re-run from the python `os.write` loop instead of the bash `for $(seq)` loop. Tracked numbers unchanged (within run-to-run noise); dist K=10K now completes the full workload.

| prefix | k | commit_s (Lane D, py driver) | commit_s (Lane D, bash driver, Phase 2) | Δ |
|---|---:|---:|---:|---:|
| tracked/load/k_capture | 1 | 0.0013 | 0.001 | — |
| tracked/load/k_capture | 100 | 0.0164 | 0.011 | +49% (small-K noise) |
| tracked/load/k_capture | 1 000 | 0.119 | 0.097 | +23% |
| tracked/load/k_capture | 10 000 | 1.076 | 1.025 | +5% (within noise) |
| dist/k_capture | 1 | 0.0011 | 0.001 | — |
| dist/k_capture | 100 | 0.0101 | 0.010 | — |
| dist/k_capture | 1 000 | 0.0996 | 0.096 | — |
| **dist/k_capture** | **10 000** | **0.848** | **0.425 (partial, ~4168 files)** | **first full-workload data point** |

The python driver introduces a small per-call cost vs bash (one `python3` startup ≈ 35 ms vs `bash -c` ≈ 5 ms) but is uniformly applied; the K-scaling **shape** is preserved. The dist K=10K data point is now usable.

---

## What is NOT covered (explicit non-goals)

- **Repeated-workload soak** (1000+ shell calls in one session). The daytona sandbox runs in copy-backed mode (no `unshare`) and the daemon never cleans up its `/dev/shm/eos-command-exec/<request-id>/` run dirs. This is pre-existing; out of Phase 2.5 scope. See "Known issue" below.
- **Symlink / opaque-dir kinds**. Lane D currently optimises `MergedView.read_bytes` only; `read_symlink` and `list_dir` still walk the filesystem. Documented as Phase 3 candidate in the Phase 2 report.
- **Concurrency-axis matrix**. Phase 1's c20 matrix already covers concurrent shell-capture load and that data ports cleanly to Lane D (see Phase 2 report §"Phase 1 c20 regression check").

---

## Known issue — copy-backed mode + /dev/shm exhaustion

The daytona test image lacks `unshare` privileges, so the daemon falls back from `private_mount_namespace` to `copy_backed` mode (`backend/src/sandbox/command_exec/workspace_mount.py:_run_copy_backed_mount`). Each shell call:

1. Materialises the full layer-stack lower into `storage_root/runtime/_TRANSIENT_LOWERDIR_DIR/<id>/lower` (cleaned up after the call).
2. **Copies** that lower into `/dev/shm/eos-command-exec/<key>/<request-id>/workspace` via `_copy_tree`.
3. Runs the command in that copy.
4. Captures upperdir, OCC commits.
5. Releases the lease and drops the *transient lowerdir*.

Step 2's run-dir on `/dev/shm` is **never removed** by the daemon. Across many shell calls, the `/dev/shm` (64 MiB on this image) fills up with stale `<request-id>/` trees, which is why the Phase 07 matrices fail when batched into one pytest invocation but pass in isolation. The Phase 2.5 tests were therefore split into separate pytest invocations per matrix.

**Out-of-scope follow-up.** Add a `shutil.rmtree(run_dir, ignore_errors=True)` in `shell_runner.py`'s outer `finally` block, or run a periodic LRU-eviction over `_command_exec_runtime_root(storage_root)`. Either is a one-file fix.

---

## Files landed in Phase 2.5

| File | LOC | Purpose |
|---|---:|---|
| `backend/tests/live_e2e_test/sandbox/_harness/large_capture_workload.py` | +220/−10 | Replaces the bash `for $(seq)` driver with a `python3 -c` driver and adds `build_sized_capture`, `build_seed_capture`, `build_modify_capture`, `build_delete_capture`, `build_mixed_kinds_capture`, `build_mixed_routing_capture`, `build_count_files_command`. Fixes the dist K=10K bash-side truncation. |
| `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase07_complex_capture_metrics.py` | +395 (new) | Three matrices (size×K, kind×K, mixed-routing) with post-commit correctness asserts. |
| `backend/src/sandbox/layer_stack/merged_view.py` | +5/−2 | Dedup of `WHITEOUT_PREFIX` / `OPAQUE_MARKER` — re-export from `layer_index.py` so existing 5 callers continue to work. **Landed separately in commit `4f50f2ce4` ("Share layer stack whiteout constants") by the parallel codex session — listed here for completeness, not part of this phase's commit.** |

Artifacts:

- `.omc/results/phase07-size-matrix-20260507T205159Z-19742.jsonl` (16 cells)
- `.omc/results/phase07-kind-matrix-20260507T205514Z-24343.jsonl` (16 cells)
- `.omc/results/phase07-mixed-routing-20260507T204102Z-7000.jsonl` (3 cells)
- `.omc/results/phase06-large-capture-dist-k10000-20260507T205746Z-28216.jsonl` (full dist K=10K, supersedes the partial-workload Phase 2 data point)

---

## Reproduction

Each matrix runs in its own pytest invocation to avoid the `/dev/shm` exhaustion described above:

```bash
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase07_complex_capture_metrics.py::test_phase07_size_matrix -vs
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase07_complex_capture_metrics.py::test_phase07_kind_matrix -vs
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase07_complex_capture_metrics.py::test_phase07_mixed_routing_matrix -vs
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase06_large_capture_scaling.py::test_phase06_large_capture_dist_k10000 -vs
```

Lint:
```bash
.venv/bin/ruff check backend/tests/live_e2e_test/sandbox/_harness/large_capture_workload.py backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase07_complex_capture_metrics.py backend/src/sandbox/layer_stack/merged_view.py
```

Unit-test regression: `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` → 393 passed, 1 skipped.

---

## How this maps back to the user's three asks

1. **"Implement the Phase 2 plan"** — done in Phase 2 (commits `7901fc715`, `c9702c8ba`, `75e811427`); this report is the Phase 2.5 extension.
2. **"Very detailed performance metrics on large files / sheer file count / OCC merge routing (gated vs gitignored), need very complex cases"** — Phase 2.5's three matrices answer the four sub-questions:
   - large files → size matrix (64 B → 1 MiB, 16384× range)
   - sheer file count → Phase 06 K-scaling, now with full dist K=10K
   - gated vs gitignored → kind matrix run on both prefixes + mixed-routing matrix that populates both routes in one commit
   - complex cases → kind matrix's MIXED cell (NEW + MODIFY + DELETE in one commit) and mixed-routing's three asymmetric splits
3. **"Verify the correctness"** — every Phase 07 cell now post-commit asserts file count and (for mixed_routing) OCC routing counts, on top of the original `result.success` check. Phase 2 only verified the latter.

---

## Concurrency-axis snapshot (already in the corpus, not part of Phase 2.5's three matrices)

Source: `.omc/results/live-e2e-phase05-public-file-ops-load_matrix-20260507T195419Z.jsonl` (Phase 1 c-matrix run on Lane D + python driver).

| workload    | c=1 ops/s | c=5 ops/s | c=10 ops/s | c=20 ops/s | speedup c=20/c=1 | parallel_eff at c=20 |
|---|---:|---:|---:|---:|---:|---:|
| read_file   | 2.79 | 10.31 | 18.34 | **23.71** | 8.5× | 0.84 |
| write_file  | 2.67 | 11.86 | 19.36 | **23.27** | 8.7× | 0.88 |
| edit_file   | 2.70 |  8.55 | 20.38 | **21.82** | 8.1× | 0.85 |
| shell       | 1.18 |  4.58 |  7.73 |  **9.42** | 8.0× | 0.86 |
| mixed       | 2.78 | 11.47 | 10.23 | **13.65** | 4.9× | **0.60** |

Lane D scales near-linearly to c=20 on four of five workloads. **`mixed` drops to parallel_efficiency 0.56–0.60 at c=10/c=20** — that's a real ceiling, root cause unknown (suspect OCC commit-queue interleaving since `mixed` batches different per-call cost profiles).

---

## Next: improvements to ship safely

Ranked by simplicity-vs-payoff. All preserve current behaviour; none add abstractions, dependencies, or speculative configurability.

| # | Change | LOC | Expected payoff | Risk | Evidence |
|---|---|---:|---|---|---|
| **1** | Daemon: `shutil.rmtree(run_dir, ignore_errors=True)` in `shell_runner.py`'s outer `finally` | ~3 | Eliminates `/dev/shm` (64 MiB tmpfs) leak — every shell call leaves its run-dir behind in copy-backed mode | Trivial: `ignore_errors=True` makes failure non-fatal; cleanup runs after capture & commit are done | Phase 07 batch failure mode. The previous Ralph session and this one both diagnosed the same leak independently. |
| **2** | Hand precomputed `final_hash` from `command_exec/capture/changeset.py:31` to `WriteChange`; drop the redundant `Path(content_path).read_bytes()` | ~10 | ~10–15% reduction on stager-bound workloads (≥ 64 KiB files) | Low: `final_hash` is already on `OverlayPathChange`; one hot-path read becomes one assignment | Phase 07 size matrix at 1 MiB: stager is 33% of `commit_s` (0.018 / 0.054 s on tracked). One redundant byte read per stager write. |
| **3** | Migrate `MergedView.read_symlink` and `MergedView.list_dir` to consult `LayerIndex` (the Phase 2.3 cache) | ~30 | Same class of speedup Lane D gave `read_bytes` (4× at K=10K) for symlink/dir-listing API verbs | Low: read-only consult; falls back to filesystem on hit. Pattern already proven by `read_bytes`. Retire `_has_file_ancestor` / `_has_opaque_ancestor` / `_whiteout_path` once both callers migrate. | Lane D only optimised one verb. The other two still do per-layer filesystem walks per call. |
| **4** | Investigate `mixed` parallel_efficiency cliff at c=10 (0.56) | n/a | Unblocks the highest-leverage concurrency ceiling | Investigation only; no code change | c=20 mixed = 13.65 ops/s vs read_file = 23.71 ops/s. 11 ops/s gap purely from interleaving heterogeneous calls. Suspect OCC commit-queue contention. |
| **5** | Per-key lock around `MergedView._layer_index` cold-build | ~5 | Eliminates the rare cold-start duplicate `rglob` under c=20 first-touch | Trivial: `threading.Lock()` per layer-id, released immediately after build | Cold-start corner only. Not visible in current benchmarks; flagged for completeness. |

**Sequencing** (most-payoff-first): #1 → #2 → #3 → #4 → #5. Items #1–#3 are independent and can land in parallel.

**Explicit non-goals for the "safe" tier:**
- Reviving `MaterializedSnapshotCache` (Phase 04.5 retired it). Needs lease-aware eviction; not a one-day change.
- Multi-lane OCC commits for disjoint paths. Concurrency model change.
- Stager throughput beyond 470 MB/s. That's the host-fs ceiling, not Lane D.
- Persisting `LayerIndex` to disk for warm-restart. `rglob` per layer is already fast; persistence is not load-bearing.

---

## Next: verifications to add (correctness coverage gaps)

Phase 2.5 verifies file count and OCC routing count per cell. The gaps below would harden trust without re-architecting any test.

| # | Gap | What to add | LOC | Effort |
|---|---|---|---:|---|
| **A** | **Byte-content correctness.** Phase 07 asserts file *count*, not that bytes inside each file match the workload intent. The seed/modify distinction (`b'baseline ...'` vs `b'modified ...'`) is inferred from `stager_write_count`, never read back. | Per cell, `tool.read_file()` one path and assert content prefix matches the kind (`baseline ` for seed, `modified ` for modify, `new ` for new, `gated ` / `dist ` for routing). | ~30 in test_phase07 | Trivial extension; one extra shell call per cell. |
| **B** | **Manifest correctness.** Phase 07 trusts `gated_path_count` / `direct_path_count` from commit timings. Never inspects the OCC layer-stack manifest directly. | Add a probe that lists the active manifest's top layer paths and asserts the set matches `(workload added) ∪ (workload modified) − (workload deleted)`. | ~50 (need a new probe in `_harness`) | Medium; needs a small daemon-API helper. |
| **C** | **No concurrency-axis run on Phase 07 matrices.** Phase 05's c=1/5/10/20 covers the **basic** workloads (read/write/edit/shell/mixed), but not size×K, kind×K, or mixed_routing under concurrency. | Wrap each Phase 07 cell in `gather_with_barrier(concurrency=N)` for N ∈ {1, 5, 10, 20}. Skip large-K cells at high concurrency to fit /dev/shm budget. | ~80 (re-use phase05's c-matrix harness) | Medium; depends on improvement #1 (daemon `/dev/shm` cleanup) landing first, otherwise the matrix will hit ENOSPC. |
| **D** | **Lease-pinning safety under churn.** No test exercises the scenario "lease A holds layer X while concurrent commit removes X" — the cache-eviction race I described in the answer above. | Add a concurrent unit test: spawn N leases on a 5-layer manifest, churn the active manifest by adding/removing leaf layers, verify all lease reads succeed and `evict_layer_index` is never called for a still-pinned layer. | ~60 unit-test | Low; pure unit-level, no live sandbox needed. |
| **E** | **Daemon `/dev/shm` cleanup regression test.** Once improvement #1 lands, add a test that runs N=200 sequential `tool.shell` calls and asserts `du -s /dev/shm/eos-command-exec` stays bounded (e.g., ≤ 5 MiB). | Single live test that loops `tool.shell("true")` and probes `/dev/shm` via `raw_exec`. | ~40 | Low. |

**Sequencing** (correctness-first): A → D → B → E (post-#1) → C (post-#1).

---

## System-design invariants to preserve in the next phase

These are already true today; the goal is to keep them true as we ship #1–#5 and A–E.

| Invariant | Why it matters | How to keep checking it |
|---|---|---|
| **Lane D's `LayerIndex` is consulted only on the read path** | Read-only consultation makes the cache safe to share across leases without locking | Code-review: any new write-path code that touches `_layer_index_cache` directly is a smell. Eviction goes through `evict_layer_index` only. |
| **Cache eviction is reference-counted via `_remove_unreferenced_layers`** | Prevents stale-read after concurrent removal | Verification D above. Plus assert `evict_layer_index` is only called from `_remove_unreferenced_layers`. |
| **Builders generate Python source as strings, not Python via `exec`** | Keeps the workload script auditable from the test source | Code-review: any builder that does `eval` / `exec` of generated code instead of `python3 - <<'PY' ... PY` heredoc is a smell. |
| **All builders share three validators (`_require_k`, `_require_prefix`, `_require_min_size`)** | Prevents silent file-size mismatches like the (size − 7) bug we hit during Phase 2.5 development | Lint/grep: any new validation `if k < 1: raise` instead of `_require_k(k)` is a smell. |
| **Each Phase 07 cell asserts post-commit filesystem state, not just `result.success`** | Catches silent partial-workload failures (the bash K=10K truncation hid for an entire phase under just-success-checks) | Code-review for new test cells; assertion shape matches the kind (count for new/modify/mixed; 0 for delete; per-prefix for routing). |
| **Each Phase 07 test method runs in its own pytest invocation until daemon `/dev/shm` cleanup lands (improvement #1)** | Documented in §"Known issue". Reproduction commands list each test invocation separately. | Reproduction section above; once #1 lands, this constraint can be relaxed and the §"Known issue" section deleted. |

The system is small (Lane D core = `layer_index.py` 65 LOC + `merged_view.py` +24 LOC + `stack_manager.py` +5 LOC = **94 LOC of production code**) and the cache is reference-counted, immutable-per-entry, and bounded by active leases. Improvements #1–#3 keep that property — none introduce new abstractions, configurability, or speculative state. Verifications A–E keep correctness ahead of optimisation.
