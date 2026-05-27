# Sandbox Subsystem Complexity Analysis

**Scope:** `backend/src/sandbox/{occ,layer_stack,overlay,ephemeral_workspace,isolated_workspace}`
**Date:** 2026-05-27

Let *L* = layer depth, *N* = changes/paths in a request, *B* = batch size,
*U* = upperdir entries, *H* = handle count, *F* = files in a layer.

## Per-subsystem complexity

### `occ/` — Optimistic Concurrency Control

| Operation | Time | Space | Notes |
|---|---|---|---|
| `path_staging.stage_group` (per path) | O(changes + content) | O(content) | Linear per change; hash check O(content). |
| `commit_transaction.revalidate_and_publish` | O(N + write bytes) | O(N) | Per-group validation independent; collect & finish are linear. |
| `commit_queue._disjoint_batches` | O(B²·avg_paths), bounded by `max_batch_size=64` | O(B·avg_paths) | Greedy bin-packing; 64²=4096 ops worst-case → not worth optimizing. |
| **`commit_queue._commit_batch` (before fix)** | **O(B·N)** | O(N) | Each item rescanned full `result.files`. |
| **`commit_queue._commit_batch` (after fix)** | **O(N)** | O(N) | Single path→FileResult map. |
| `changeset.compute_changeset_id` | O(N · payload) | O(payload) | sha256 of canonical JSON. |

### `layer_stack/`

| Operation | Time | Space |
|---|---|---|
| `MergedView.read_bytes` | O(L) lookups; +O(P) for ancestor checks | O(1) (cached per-layer index) |
| `MergedView.list_dir` | O(L · (files+whiteouts+opaque)) per call | O(direct children) |
| `MergedView.iter_paths` | O(Σ_layer files · path_depth) | O(visible files) |
| `MergedView.project` | O(total bytes) + per-layer `rglob` sorted | O(layer entries) |
| `LayerCheckpointSquasher.plan` | O(L) | O(L) |
| `LayerPublisher.publish_layer` | O(N + fsync tree) | O(N) |
| `aggregate_layer_changes` | O(N log N) (sorted output) | O(N) |
| `manifest_prefix_before_plan` | O(L) | O(1) |

### `overlay/`

| Operation | Time | Space |
|---|---|---|
| `capture.walk_upperdir` | O(U), streamed via `os.walk` | O(emitted_opaque_dirs) — already optimized away from `sorted(rglob("*"))` |
| `lifecycle.acquire` / `release_overlay` | O(scratch_size for rmtree) | O(1) |
| `kernel_mount.mount_overlay` | O(L) (mount string assembly) | O(L) |

### `ephemeral_workspace/`

| Operation | Time | Space |
|---|---|---|
| `pipeline.run_tool_call` | O(U + N + publish bytes) | O(U + N) |
| `_upperdir_total_bytes` | O(min(U, sample_limit)) | O(1) — capped at 5000 entries |
| `workspace_publish._apply_workspace_capture` | O(N) | O(N) |

### `isolated_workspace/`

| Operation | Time | Space |
|---|---|---|
| `pipeline.run_tool_call` | O(1) routing; O(exec) for command | O(captured paths) |
| `ttl_sweep` / `_sampler_loop` | O(H) per tick | O(H) |
| `_check_host_capacity` | O(1) | O(1) |
| `_exit_open_agents` | O(H) | O(H) |

---

## Fix applied

**File:** `backend/src/sandbox/occ/commit_queue.py:189-208`

`_commit_batch` previously rescanned the combined `result.files` once per batched
item — O(B·N), up to ~32× over-work at `max_batch_size=64`. Now builds a single
`path→FileResult` dict and looks up each item's groups directly: O(N) total.
Correctness preserved because `_disjoint_batches` guarantees per-path uniqueness
across the combined changeset. All 68 `test_occ/` tests pass.

```python
# Before — O(B·N)
for item in batch:
    paths = _path_set(item.prepared)
    files = tuple(file for file in result.files if file.path in paths)
    ...

# After — O(N)
files_by_path = {file.path: file for file in result.files}
for item in batch:
    files = tuple(
        files_by_path[group.path]
        for group in item.prepared.path_groups
        if group.path in files_by_path
    )
    ...
```

### Not fixed (intentional)

- `_disjoint_batches` is O(B²) bounded by `max_batch_size=64` — 4096 ops worst-case.
- `MergedView.list_dir` / `iter_paths` are O(L · entries) — sublinear would need a
  per-prefix or manifest-aggregate index, a structural redesign rather than a
  localized fix (see "Structural opportunities" below).

---

## System-level take

### Strengths

- **Architectural skeleton is sound:** lease-based GC, CAS publish, disjoint-batch
  commit queue, per-layer immutable digests for idempotency. Right primitives,
  not workarounds.
- **Telemetry hygiene is unusually good.** `TimingKey` buckets everywhere,
  `monotonic_now()` at every phase boundary. The team's own memory notes show
  the numbers actually get read (svc.cmd breakdown, overlay 16-layer root cause,
  codeact cost split). That's how perf work compounds.
- **Several optimizations are clearly post-mortem-driven:** `walk_upperdir`
  switched from `sorted(rglob("*"))` to streamed `os.walk` for OOM;
  `_upperdir_total_bytes` capped at 5000 entries; small-file copy threshold
  tuned (16 KiB); shell pre-mount squash to bound mount cost.

### Structural opportunities (in priority order)

1. **`MergedView` has no aggregate index.** Every `read_bytes` walks all L
   layers' cached indexes. Fine at L=10, painful at L=200. The reason
   `_run_shell_pre_mount_maintenance` exists is that depth is a first-class cost.
   A manifest-level merged index (`path → (layer_id, kind)`, invalidated on
   publish) would make reads O(1) and remove pre-mount squash from the critical
   path. **Biggest structural win.**
2. **No bulk-write staging path.** `_FileSystemLayerChangeStager.write` does one
   `write_bytes` per change to `NNNNNN.bin`; `_fsync_tree_files` then fsyncs
   every file individually. For a 10k-write commit, that's 20k syscalls serially
   under the publisher lock. A bundled-tar or io_uring staging mode would be
   10–100× faster on that workload.
3. **`commit_to_workspace` and `build_checkpoint` do full-byte copies.**
   `project(share_inodes=False)` calls `shutil.copy2` per file. The
   `share_inodes=True` mode exists but isn't used in the commit/squash paths.
4. **OCC revalidation reads paths serially.** Under the publisher lock, every
   path in a batched changeset is re-read via `LayerSnapshotReader.read_bytes`
   one at a time. With B=64 × 10 paths each, that's 640 serialized reads inside
   the global lock. Bulk-read across the merged view would shorten lock-hold time.
5. **Squash is reactive, depth-triggered.** A shell command at the wrong moment
   pays full squash latency under `_shell_mount_maintenance_lock`. Background
   squash daemon off the critical path would smooth tail latency.

### Cultural read

This codebase has matured past "add a profiler and react." The local wins are
done. The next gear is structural — one merged index, one write-bundle staging,
one async squasher. Those are bigger PRs but each removes a category of latency,
not a constant factor. The N²→N fix applied above is the right shape for what's
left at the micro level: small, surgical, measurable. Don't expect many more.
