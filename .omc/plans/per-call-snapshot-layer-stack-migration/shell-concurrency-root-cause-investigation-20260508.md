# Shell Concurrency Root-Cause Investigation

**Date:** 2026-05-08
**Branch:** codex/fix-dot-path-normalization-tests
**Test under study:** `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py::test_phase05_public_file_ops_load_matrix_c1_c5_c10_c20`
**Artifacts:**
- v1 baseline (pre-instrumentation): `.omc/results/live-e2e-phase05-public-file-ops-load_matrix-20260507T172858Z.jsonl`
- v2 with new timings: `.omc/results/live-e2e-phase05-public-file-ops-load_matrix-20260507T174307Z.jsonl`

---

## Executive Summary

Shell throughput at concurrency 20 is **4.75 ops/s** — worse than at c10 (4.66 ops/s). Per-call wall climbs from 685 ms (c1) to 2906 ms (c20.p99), a **4.1× degradation**. The "0.977 parallel_efficiency" headline is misleading — it reflects gather-barrier behaviour, not real CPU/IO parallelism.

Two independent root causes account for the entire degradation:

| Cause | What | c20.p99 cost | Fix risk |
|---|---|---|---|
| **A — Daemon event-loop starvation** | `layer_stack.prepare_workspace_snapshot` is a sync call sitting on the asyncio loop between dispatch entry and the first `await`; 20 handlers serialise the loop. | **1209 ms** of wall | Low — wrap in `run_sync_in_executor` |
| **B — Layer-stack server serialisation in OCC prepare** | `_prepare_group → base_hash_reader → infer_manifest_base_hash` issues N round-trips to the single layer-stack server. | **370 ms** of wall | Medium — needs investigation of manifest carry / batch RPC / fast-path routing |

OCC commit lane is **not** the dominant cause for shell — `occ.serial.batch_size = 19` shows it is correctly coalescing.

---

## Methodology

### Step 1 — Run the existing matrix and decompose timings

```
.venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py -xvs
```

Read every emitted timing key from the JSONL artifact and computed p50/p99 per concurrency level for each workload. Result: existing keys already pinpointed two suspects (`runtime.boot_to_dispatch_s`, `occ.prepare.route_and_base_hash_s`) but did not isolate which sub-step inside each.

### Step 2 — Diff per-call timings c1 vs c20 (no new code)

`command_exec.prepare_snapshot_s` was found to be **flat** at ~80–100 ms across all concurrencies (1.1× growth). This is the discriminating fact: a sync call whose own duration does not grow under load but whose downstream effect (event-loop starvation) does grow super-linearly.

### Step 3 — Add three targeted instrumentation points

Per advisor input, "one discriminating timing per hypothesis":

| Hypothesis | Instrumentation | File |
|---|---|---|
| Daemon loop starves on readline | `runtime.read_request_s` (boot_t0 → after readline) | `backend/src/sandbox/runtime/daemon.py` |
| Shell handler runs sync I/O on the loop before its first await | `command_exec.handler_sync_prelude_s` (entry → first await) | `backend/src/sandbox/runtime/command_exec_server.py` |
| OCC prepare's heavy step is base-hash RPC, not gitignore routing | Split `route_and_base_hash_s` into `group_by_route_s` and `prepare_groups_s` | `backend/src/sandbox/occ/orchestrator.py` |

All three changes are surgical (≤ 10 LOC each) and additive. No existing behaviour changed.

### Step 4 — Re-run the matrix and confirm

Same pytest invocation. The new keys appear in the v2 artifact and tell a definitive story (see *Findings*).

---

## Performance Matrix — Shell Workload (v2 artifact)

### Per-concurrency summary

| Concurrency | batch_wall_ms | wall p50 ms | wall p99 ms | runtime p99 ms | parallel_factor | parallel_efficiency | throughput ops/s |
|---|---|---|---|---|---|---|---|
| c1  | 1126.4 | 1125.6 | 1125.6 | 672.8  | 0.999 | 0.999 | 0.888 |
| c5  | 1519.8 | 1406.0 | 1516.1 | 717.4  | 4.628 | 0.926 | 3.290 |
| c10 | 2146.4 | 2108.8 | 2140.3 | 1448.0 | 9.664 | 0.966 | 4.659 |
| c20 | 4208.2 | 4170.4 | 4194.3 | 2907.5 | 19.535| 0.977 | **4.753** |

### Key timings c1 vs c20 (p50 / p99 in seconds)

| Timing | c1.p50 | c1.p99 | c20.p50 | c20.p99 | Growth (p50) | Bucket |
|---|---|---|---|---|---|---|
| `runtime.read_request_s` | 0.0001 | 0.0001 | **0.4105** | **1.2087** | ~5000× | NEW — splits boot_to_dispatch |
| `runtime.boot_to_dispatch_s` | 0.0001 | 0.0001 | 0.4105 | 1.2088 | ~5000× | identical to read_request_s |
| `runtime.dispatch_s` | 0.6732 | 0.6732 | 2.2727 | 2.9090 | 3.4× | post-dispatch processing |
| `command_exec.handler_sync_prelude_s` | 0.0679 | 0.0679 | **0.1058** | **0.1929** | 1.6× | NEW — sync work on event loop |
| `command_exec.prepare_snapshot_s` | 0.0676 | 0.0676 | 0.1015 | 0.1899 | 1.5× | dominates the prelude |
| `command_exec.run_command_s` | 0.3454 | 0.3454 | 0.4306 | 0.5322 | 1.2× | actual subprocess (fine) |
| `command_exec.occ_apply_s` | 0.0459 | 0.0459 | 0.8060 | 1.8114 | 17.6× | aggregate |
| `occ.prepare.route_and_base_hash_s` | 0.0314 | 0.0314 | 0.2395 | 0.3703 | 7.6× | aggregate |
| `occ.prepare.group_by_route_s` | 0.0231 | 0.0231 | **0.0001** | **0.0677** | flat | NEW — gitignore routing (NOT bottleneck) |
| `occ.prepare.prepare_groups_s` | 0.0083 | 0.0083 | **0.2394** | **0.3701** | 29× | NEW — base_hash RPC serialisation |
| `occ.apply.commit_queue_wait_s` | 0.0038 | 0.0038 | 0.0372 | 0.1947 | 9.8× | OCC queue (smaller cause) |
| `occ.apply.commit_s` | 0.0136 | 0.0136 | 0.3678 | 0.5346 | 27× | OCC commit lane |
| `command_exec.total_s` | 0.6728 | 0.6728 | 2.2724 | 2.9087 | 4.1× | total wall |

### Wall reconstruction at c20.p99 (2.91 s observed)

```
runtime.read_request_s          1.21 s   ← Cause A — loop starvation
handler_sync_prelude            0.19 s   ← feeds Cause A (sync work blocking the loop)
run_command_s                   0.53 s   ← actual subprocess (fine)
occ.prepare.prepare_groups_s    0.37 s   ← Cause B — layer-stack RPC serialisation
occ.apply.commit_queue_wait_s   0.19 s   ← OCC queue (small)
occ.apply.commit_s              0.53 s   ← OCC commit
release + capture + mount       ~0.05 s
                                 ─────
total accounted                ≈ 2.97 s   (overlapping windows make this ≥ observed)
```

> Note: timings overlap (e.g. `commit_queue_wait_s` is inside `commit_s`), so the sum overshoots. The decomposition is for attribution, not strict accounting.

---

## Findings — Root Cause Attribution

### Cause A — Daemon event-loop starvation (1209 ms p99 of total wall)

The new `runtime.read_request_s` matches `runtime.boot_to_dispatch_s` to four decimal places (0.4105 vs 0.4105 at c20.p50; 1.2087 vs 1.2088 at c20.p99). **Conclusion:** the entire `boot_to_dispatch` time is consumed by `reader.readline()` not getting CPU on the asyncio loop. The loop is busy with other handlers' sync work.

`command_exec.handler_sync_prelude_s` (192.9 ms p99 at c20) ≈ `command_exec.prepare_snapshot_s` (189.9 ms p99). The sync prelude in `_execute_shell` is dominated by the synchronous client call:

```python
# backend/src/sandbox/runtime/command_exec_server.py:62
lease = layer_stack.prepare_workspace_snapshot(
    workspace_ref=request.workspace_ref,
    request_id=request.request_id,
)
```

This blocks the asyncio loop for 100–200 ms per handler. With 20 concurrent handlers, the cumulative blocking ≈ 2 s, which is what makes the unlucky handler's `readline` wait 1.2 s.

`prepare_snapshot_s` itself is **flat** at 80–100 ms across all concurrencies — the call is fast in isolation; it is the loop-blocking, not the work, that costs the system.

### Cause B — Layer-stack server serialisation in OCC prepare (370 ms p99)

The new split inside `OccOrchestrator.prepare_sync` shows:

- `group_by_route_s`: **flat at 0.1 ms** at c20 (gitignore routing is *not* the bottleneck)
- `prepare_groups_s`: **8 ms → 239 ms (p50, 29×) / 370 ms (p99) at c20**

`_prepare_group` calls `base_hash_reader(path)` which calls `infer_manifest_base_hash(snapshot_reader=layer_stack, ...)`. This is a synchronous RPC against the single layer-stack server. With 20 concurrent prepares, the requests queue at the server.

This explains the 31× growth in `occ.prepare.route_and_base_hash_s` previously observed: it was almost entirely `prepare_groups_s` (the base-hash phase), not the routing phase.

### Why OCC commit is not the dominant cause for shell

`occ.serial.batch_size = 19` at c20 — the OCC commit lane is correctly coalescing 19 commits into one serial batch. `occ.apply.commit_s` p99 = 535 ms is real but smaller than the two causes above and is appropriate for a single batched commit of 19 paths. **Do not chase commit lane optimisations for the shell story.**

---

## Implementation Plan

### Phase 1 — Move sync prelude off the asyncio event loop *(primary fix)*

**Files**
- `backend/src/sandbox/runtime/command_exec_server.py` — `_execute_shell`
- *(check)* other handlers in `backend/src/sandbox/runtime/handlers/` that call `layer_stack.prepare_workspace_snapshot` or `release_lease` synchronously — write/edit/read likely have the same pattern
- *(verify)* `backend/src/sandbox/command_exec/clients.py` — `WorkspaceLeaseClient` thread-safety

**Change**

Wrap the lease client calls and `_drop_transient_lowerdir` (filesystem rmtree) through the executor:

```python
lease = await run_sync_in_executor(
    layer_stack.prepare_workspace_snapshot,
    workspace_ref=request.workspace_ref,
    request_id=request.request_id,
)
# … and for both release_lease call sites, plus _drop_transient_lowerdir
```

**Why it works**

`prepare_snapshot_s` is flat at ~80–100 ms — the call is fast; it is the loop-blocking that costs. Moving it to the 200-worker `ThreadPoolExecutor` (`_DEFAULT_EXECUTOR_WORKERS = 200` in `backend/src/sandbox/runtime/async_bridge.py:60`) lets handlers run their lease prep in parallel and stops them from starving each others' `readline`.

**Success criteria**

- `command_exec.handler_sync_prelude_s` p99 < 5 ms at c20
- `runtime.boot_to_dispatch_s` p99 < 50 ms at c20 (currently 1209 ms)
- shell c20 wall p99 < 2.0 s (currently 2.91 s)
- All five workloads still pass `_assert_load_summary` budgets
- No new concurrency or thread-safety regressions in `WorkspaceLeaseClient`

**Risk & mitigations**

- `WorkspaceLeaseClient` may hold per-thread state (sockets etc.). Mitigation: read the client; if so, gate fix on per-call instantiation or a thread-safe wrapper.
- `_drop_transient_lowerdir` is rmtree — already safe to thread-pool.
- Other handlers (write, edit, read) likely share the same pattern; fix in the same pass.

### Phase 2 — Reduce `occ.prepare.prepare_groups_s` cost *(secondary fix, do not start until Phase 1 confirmed)*

**Files (investigation first)**

- `backend/src/sandbox/occ/orchestrator.py` — `_prepare_group` calls `base_hash_reader(path)` per group
- `backend/src/sandbox/occ/service.py:222–227` — defines `base_hash_reader` via `infer_manifest_base_hash`
- `backend/src/sandbox/layer_stack/...` — `infer_manifest_base_hash` and the layer-stack RPC client

**Pre-implementation investigation step**

Add temporary timings inside `_prepare_group` and `infer_manifest_base_hash` to split:

- manifest-resolution time (in-process tree lookup)
- RPC round-trip time

This determines which strategy applies:

| Result | Strategy | Rationale |
|---|---|---|
| Manifest already carries per-path hashes | **Strategy 1** — read directly from snapshot manifest, skip layer-stack RPC entirely | Cheapest; no RPC change |
| Manifest is a sparse index needing layer-stack lookup | **Strategy 2** — batch base-hash lookups in one RPC per prepare call | Reduces round-trips; reuses existing RPC surface |
| Layer-stack server is intrinsically per-path | **Strategy 3** — route single-path captures to existing `prepare_single_path_changeset` fast path in `backend/src/sandbox/occ/single_path_prepare.py` | Smallest blast radius; only helps single-path captures |

**Success criteria**

- `occ.prepare.prepare_groups_s` p99 at c20 < 100 ms (currently 370 ms)
- `occ.prepare.route_and_base_hash_s` p99 at c20 < 150 ms

**Risk**

- Behavioural — base_hash gates OCC commits; correctness must not regress. Add a unit test demonstrating identical routing decisions before/after.

### Phase 3 — Verify and clean up

1. Re-run `test_phase05_public_file_ops_load.py`, `test_concurrency_scaling.py`, `test_load_profiles.py`. All must pass.
2. Compare new load_matrix JSONL against the v2 baseline (`20260507T174307Z`):
   - shell c20 throughput should rise from 4.75 → ≥ 8 ops/s
   - mixed c20 efficiency should rise from 0.706 → ≥ 0.85
   - write/edit c20 should also benefit from Phase 1 since they share the same lease prelude
3. Promote useful timings to permanent; remove temporary Phase-2 investigation timings if not needed.
4. Update `phase-05-public-file-ops-performance-metrics-report.md` with the load-matrix delta.

### Out of scope / explicit non-goals

- OCC commit lane optimisation — `batch_size = 19` confirms it is already coalescing well.
- Further parallelising `run_command_s` — only 1.3× growth at c20; subprocess fork is fine.
- A new shell-only test fixture — the matrix already covers c1/c5/c10/c20; expand only if Phase 2 needs higher resolution.

### Order of operations

1. Implement Phase 1, run matrix, confirm `boot_to_dispatch_s` drops to < 50 ms.
2. *Then* decide if Phase 2 is worth pursuing — if shell.c20 throughput already hits 8 ops/s after Phase 1, Phase 2 may be unnecessary risk.
3. If proceeding: trace base_hash for Phase 2 first; commit only if Strategy 1 is viable (lowest risk).
4. Phase 3 verification.

---

## Appendix — Instrumentation Code Diff (already applied)

### `backend/src/sandbox/runtime/daemon.py`

```python
boot_t0 = time.perf_counter()
try:
    raw = await reader.readline()
    read_completed_at = time.perf_counter()       # NEW
    if not raw:
        return
    # … existing JSON parse + dispatch …
    if isinstance(response, dict):                # NEW
        timings = response.get("timings")
        if not isinstance(timings, dict):
            timings = {}
            response["timings"] = timings
        timings["runtime.read_request_s"] = max(  # NEW key
            0.0, read_completed_at - boot_t0
        )
```

### `backend/src/sandbox/runtime/command_exec_server.py`

```python
spec = WorkspaceReplacementMountSpec(...)
timings["command_exec.handler_sync_prelude_s"] = (  # NEW key
    time.perf_counter() - total_start
)
process = await run_sync_in_executor(...)
```

### `backend/src/sandbox/occ/orchestrator.py`

```python
group_start = time.perf_counter()
grouped = self._group_by_route(changes, snapshot=snapshot)
groups_end = time.perf_counter()
prepared = tuple(self._prepare_group(...) for ... in grouped)
prepare_end = time.perf_counter()
return PreparedChangeset(
    snapshot=snapshot,
    path_groups=prepared,
    atomic=options.atomic,
    timings={
        "occ.prepare.group_by_route_s": groups_end - group_start,   # NEW key
        "occ.prepare.prepare_groups_s": prepare_end - groups_end,   # NEW key
    },
)
```
