# Shell Concurrency Phase 1 — Implementation Report

**Date:** 2026-05-08
**Branch:** codex/fix-dot-path-normalization-tests
**Source plan:** `.omc/plans/per-call-snapshot-layer-stack-migration/shell-concurrency-root-cause-investigation-20260508.md`
**Verdict:** Phase 1 lands a meaningful win — shell c20 throughput +47% (4.75 → 6.97 ops/s), with no regressions across the matrix. The plan's stated 8 ops/s shell c20 target is missed by 13%; the gap is documented as a deliberate non-goal of this iteration. Phase 2 is recommended for skip.

---

## Headline Results — V3D vs V2 baseline (c20 throughput)

| Workload | V2 | V3D | Δ |
|---|---|---|---|
| read_file | 17.30 ops/s | 17.75 ops/s | **+3%** |
| write_file | 14.58 ops/s | 15.51 ops/s | **+6%** |
| edit_file | 10.95 ops/s | 10.76 ops/s | -2% (within noise) |
| **shell** | **4.75 ops/s** | **6.97 ops/s** | **+47%** |
| mixed | 7.59 ops/s | 9.77 ops/s | **+29%** |

Shell c20 wall p99: **4194 ms → 2862 ms** (-32%).
Shell c20 runtime p99 (server-side): **2907 ms → 1687 ms** (-42%).

All workloads c20 fit default budgets (batch ≤ 12000 ms, wall_p99 ≤ 7000 ms, runtime_p99 ≤ 4000 ms).

---

## Path to the answer

The plan asserted "loop blocking is the dominant cost; wrap sync prelude in `run_sync_in_executor` to fix it." That premise was correct in direction but missed a hidden constraint: per-call materialise cost grows super-linearly under unbounded parallelism because of filesystem-metadata contention. Multiple iterations confirmed this:

| Attempt | Change | shell c20 thru | Verdict |
|---|---|---|---|
| V2 baseline | (none) | 4.75 ops/s | n/a |
| V3A | Executor wrap on shell + write + edit handlers | 3.04 ops/s | **regression** — 20 parallel materialise saturated FS metadata, materialise_s p99 188 → 8689 ms (46×) |
| V3A + hardlink | Same wraps + `MergedView.materialize(link_ok=True)` | 3.04 ops/s | hardlink halved per-call cost (8689 → 3436 ms p99) but FS still contended |
| V3B | + `asyncio.Semaphore(4)` on prepare | 2.77 ops/s | sem wait dominates: prepare_snapshot_s p99 4757 ms vs 3457 ms unbounded |
| V3C | sem=2 | 3.02 ops/s | same losing curve |
| **V3D (final)** | **Revert shell wrap; keep write/edit wraps + hardlink + timing fix** | **6.97 ops/s** | **+47% vs v2** |

V3D's win comes from two clean sources:

1. **Hardlink materialise** (always-on, opt-in via `link_ok=True`). Halves per-call materialise cost at every concurrency tier. Pure win.
2. **Loop-unblock for write/edit handlers** (executor wraps). write c20 `runtime.read_request_s` p99: 207 → 3 ms; edit: 401 → 14 ms. The shell handler stays sync because its dominant cost (materialise) is FS-bound and parallelising it does net harm.

Mixed-workload c20 +29% is the secondary effect: with write/edit no longer holding the loop, mixed batches process more cleanly.

---

## Files landed in V3D

| File | Change |
|---|---|
| `backend/src/sandbox/layer_stack/merged_view.py` | `MergedView.materialize(link_ok: bool = False)`. New `_link_or_copy` helper using `os.link` with EXDEV/EPERM fallback. `_apply_layer` threads the flag through. Default behaviour (byte-copy) preserved for `manager.materialize()` public API and `SquashWorker`. |
| `backend/src/sandbox/layer_stack/stack_manager.py` | `prepare_workspace_snapshot` calls `materialize(..., link_ok=True)` — its lowerdir is mounted overlay-readonly (or copy-tree'd to a separate merged dir in copy-backed mode), so sharing inodes is safe. |
| `backend/src/sandbox/runtime/handlers/write_handler.py` | `services.manager.acquire_snapshot_lease`, `services.layer_stack.read_bytes`, `prepare_single_path_changeset`, `services.manager.release_lease` (in finally) all dispatched via `run_sync_in_executor`. The `read_base_hash` closure runs inside the worker thread because `prepare_single_path_changeset` is wrapped — `nonlocal` int updates remain safe under GIL. |
| `backend/src/sandbox/runtime/handlers/edit_handler.py` | Same wrap pattern (no closure since edit doesn't pass `base_hash_reader`). `_apply_edits` stays on the loop (small CPU work). |
| `backend/src/sandbox/runtime/command_exec_server.py` | `command_exec.handler_sync_prelude_s` capture moved BEFORE the first await (was previously placed AFTER `prepare_workspace_snapshot`, which happened to be the dominant cost being measured). Pure measurement-clarity fix; no behavioural change. |

---

## Files NOT landed (explicit non-goals)

- **Shell handler executor wrap** (`command_exec_server._execute_shell` prepare/release/_drop wraps + semaphore). v3a/v3b/v3c proved this is net negative: parallelising materialise on FS-bound ops loses more than the loop-unblock gains.
- **`MaterializedSnapshotCache` revival** (Phase 02 → Phase 04.5 retired it). Reopening that decision requires user authorisation; surfaced below.
- **Phase 2 (base-hash RPC reduction).** v3d evidence: `occ.prepare.prepare_groups_s` shell c20 p99 dropped from 370 ms → 154 ms naturally with the loop unblocked. Remaining headroom is small (~100 ms wall on a 1687 ms total) — Phase 2 cost outweighs benefit.

---

## Per-workload c20 read_request_s p99 (loop-unblock evidence)

| Workload | V2 | V3D | Δ |
|---|---|---|---|
| read_file | 1.1 ms | 1.8 ms | flat (already unblocked) |
| write_file | 207.8 ms | **2.8 ms** | **74×** |
| edit_file | 401.4 ms | **13.8 ms** | **29×** |
| shell | 1208.7 ms | 416.1 ms | **3×** (sync handler benefits indirectly from less write/edit loop pressure) |
| mixed | 524.8 ms | 355.8 ms | 1.5× |

The shell c20 read_request_s drops only 3× because the shell handler still serialises on the loop for prepare_workspace_snapshot. The remaining 416 ms p99 is the queue depth for 20 sequential prepares × ~90 ms each.

---

## Reproduction & verification

1. **Unit tests**: `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q --deselect backend/tests/unit_test/test_sandbox/test_command_exec/test_edit_snapshot_byte_derivation.py::test_in_workspace_edit_submits_write_change_with_derived_bytes` → 376 passed, 1 skipped, 1 deselected. The deselected test was failing on HEAD before any Phase 1 change (verified via `git stash`); it patches `apply_changeset` but the edit handler calls `commit_prepared_changeset`.
2. **Lint**: `.venv/bin/ruff check backend/src/sandbox/runtime/command_exec_server.py backend/src/sandbox/runtime/handlers/write_handler.py backend/src/sandbox/runtime/handlers/edit_handler.py backend/src/sandbox/layer_stack/merged_view.py backend/src/sandbox/layer_stack/stack_manager.py` → clean.
3. **Load matrix (default budgets)**: `.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py -xvs` → passes; artifact at `.omc/results/live-e2e-phase05-public-file-ops-load_matrix-20260507T183743Z.jsonl` (V3D) and `.omc/results/live-e2e-phase05-public-file-ops-load_matrix-20260507T184*Z.jsonl` (default-budget run).

---

## Recommendation to the user

1. **Land V3D as Phase 1 outcome.** Real wins on 4/5 workloads, no regressions; tests pass under default budgets.
2. **Skip Phase 2.** Its target metric already dropped 2.4× as a side effect of Phase 1; further work on it isn't worth the correctness risk on base-hash routing.
3. **Decide on architectural follow-up to reach the original 8 ops/s shell target.** Two paths require user authorisation:
   - **Path A (cache):** Reopen Phase 04.5's `MaterializedSnapshotCache` retirement with new evidence that per-call materialise is the residual c20 ceiling. A cache keyed by `(N, root_hash)` would make repeat-snapshot shells O(1) materialise.
   - **Path B (API-layer concurrency):** Cap concurrent shells per layer-stack root at the API surface; document the cap and accept lower peak shell parallelism.
   Each is a separate plan; not in scope here.
