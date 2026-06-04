# Rust Parity Audit — Performance Properties (O(1) lowerdir CoW, O(n·delta) upperdir, fast mount)

Domain: sandbox. Area key: `perf`.

Scope: confirm the Rust port preserves the storage/space/mount complexity
properties of the Python ground truth, and confirm a benchmark exercises the
Rust daemon path. This is an architectural-property check judged from the
mount/copy strategy in code + the bench scripts, not from raw timing.

> NOTE ON GROUND TRUTH: the Python sandbox runtime was **deleted from the
> working tree** in commit `37c13f3db` ("remove legacy python sandbox runtime
> subsystems"). It remains the behavioral ground truth. All Python file:line
> anchors in this report are read from the parent commit `a8c987845` via
> `git show a8c987845:<path>`. The architecture docs under
> `docs/architecture/sandbox/*.html` still describe the intended dynamics and
> are present in-tree.

---

## Ground truth

### Space-complexity invariant (the headline claim)
- `docs/architecture/sandbox/overview.html` "O(1) space for N concurrent
  operations": *"The repository is copied once into `B000001-base`. N parallel
  agents share that base as read-only lowerdir; each operation pays only for its
  changed bytes in its private upperdir. Disk cost is
  `O(repo) + O(N × changed_bytes)`, not `O(N × repo)`."*
- `docs/architecture/sandbox/layerstack.html` 2.3 Projection: *"A snapshot is
  O(1): a lease plus a list of existing layer paths, not a rendered tree…
  every snapshot is constant-cost regardless of repository size."* Anchored to
  `backend/src/sandbox/layer_stack/stack.py:105-129`.

### O(1) snapshot (Python `acquire_snapshot`)
`backend/src/sandbox/layer_stack/stack.py:108-135` (parent `a8c987845`): acquires
a lease over the current manifest under a lock, then maps `manifest.layers` to
existing on-disk `layer_paths` (`self._layer_path(layer).as_posix()`). No tree is
rendered. Returns `LayerStackSnapshotLease(lease_id, manifest_version, root_hash,
manifest, layer_paths, timings={"layer_stack.acquire_snapshot.total_s": ...})`.

### Upperdir is per-op + O(writes) (Python capture + writable dirs)
- `backend/src/sandbox/overlay/writable_dirs.py:46-52` — `allocate_overlay_writable_dirs`
  makes `run_dir/upper` + `run_dir/work` **per overlay**.
  `OVERLAY_WRITABLE_ROOT = "/eos/mount"`, no fallback (`writable_dirs.py:13-43`).
- `backend/src/sandbox/overlay/capture.py:19-32,49-89` — capture walks **only the
  upperdir** with `os.walk()`; "changed-data cost is tied to the operation's
  writes rather than repository size" (overlay.html 3.3).

### Fast kernel mount + manifest pointer-swap
- `backend/src/sandbox/overlay/kernel_mount.py:49-75` — `mount_overlay` uses
  `fsopen("overlay")`, one `fsconfig_string(fsfd, b"lowerdir+", layer)` per layer
  in **newest-first** order (first = highest priority), then `upperdir`,
  `workdir`, `fsconfig_create`, `fsmount`, `move_mount(workspace_root)`. Mount
  target is `workspace_root`, **not** `/`.
- `backend/src/sandbox/overlay/kernel_mount.py:139-198` — `validate_mount_inputs`
  opens `O_DIRECTORY|O_NOFOLLOW` fds and passes **lowerdirs** as
  `/proc/self/fd/N`; upper/work/mountpoint stay real paths
  (`move_mount(2)` rejects fd symlinks as destination; overlayfs rejects fd-backed
  upper/work).
- `backend/src/sandbox/layer_stack/publisher.py:49-138` — publish stages a layer,
  fsyncs, renames into `layers/`, CAS-re-reads the manifest, then atomically
  writes a new manifest with the new layer **prepended** (newest-first). This is
  the atomic-pointer-swap (CAS) commit, not a deep copy (layerstack.html 2.4).

### Base copied once = O(repo) (Python workspace base)
- `backend/src/sandbox/layer_stack/workspace_base.py:31-32,82-141` — base built
  once as `B000001-base`; walks the workspace, rejects special/unstable files,
  writes one base layer + manifest version 1 + `workspace.json`.

### Depth-cap constants (auto-squash bounds the read-amp / mount(8) limit)
- `backend/src/sandbox/occ/service.py:34` — `AUTO_SQUASH_MAX_DEPTH = 100`.
- `backend/src/sandbox/occ/maintenance.py:48-60` — after publish, squash only if
  `active.depth <= max_depth` is **false** (operator `<=` → skip).
- `backend/src/sandbox/layer_stack/squash.py:61-93` — plan acceptance operators:
  `active.depth <= max_depth` → None; `len(entries) >= active.depth` → None;
  `active.depth - len(entries) < min_reduction` → None; final
  `len(entries) > max_depth and all(len(seg) <= max_depth …)` → None. Default
  `min_reduction=1`; `can_squash()` passes `2`.
- `backend/src/sandbox/ephemeral_workspace/pipeline.py:455-463` — a **separate**
  shell-pre-mount squash path with default `64`, env-overridable via
  `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`. (Docs note mount(8) ≈16 / mount(2) ≈200 as
  the *reasons* a cap exists; neither 16 nor 200 is a code constant — the real
  caps are 100 and 64.)

### Benchmarks (in-tree, Rust daemon)
- `backend/scripts/bench_rust_daemon_phase2.py:56,93,202-231,253-304` — uploads a
  locally packaged `eosd` (`EOSD_REMOTE_PATH = "{RUNTIME_ROOT}/eosd"`,
  default artifact `sandbox/dist/eosd-linux-amd64`), seeds a LayerStack with
  `B000001-base`, starts the Rust daemon.
- `backend/scripts/bench_rust_daemon_phase3.py:305-323,339-427` — starts the
  Rust daemon, builds the base from the image workspace via
  `api.build_workspace_base`, then measures `api.v1.exec_command` (no-op +
  small-write publish), `api.v1.glob`, `api.v1.grep`, and a **1/3/5/10
  concurrent** shell-exec load matrix (no-op + unique-write), plus daemon RSS
  before/between/after.

---

## Rust mapping

| Python (parent `a8c987845`) | Rust |
|---|---|
| `overlay/kernel_mount.py:49-75 mount_overlay` | `sandbox/crates/eos-overlay/src/kernel_mount.rs:106-137` |
| `overlay/kernel_mount.py:139-198 validate_mount_inputs` | `kernel_mount.rs:192-247 ValidatedMountInputs::open` (+ `fd_path` 282-284) |
| `overlay/writable_dirs.py:46-52` | `eos-overlay/src/writable_dirs.rs:65-79 allocate_overlay_writable_dirs` |
| `overlay/capture.py:49-89 walk_upperdir` | `eos-overlay/src/path_change.rs:155-269 capture_upperdir` |
| `layer_stack/stack.py:108-135 acquire_snapshot` | `eos-layerstack/src/stack.rs:343-372 acquire_snapshot` |
| `layer_stack/publisher.py:49-138 publish_layer` | `eos-layerstack/src/stack.rs:618-681 publish_layer` |
| `layer_stack/view.py project()` | `eos-layerstack/src/stack.rs:132-139 MergedView::project` |
| `layer_stack/workspace_base.py:31-141` | `eos-layerstack/src/workspace_base.rs:22,102-171,296-360` |
| `layer_stack/squash.py:61-93 plan` | `eos-layerstack/src/squash.rs:160-209 plan` |
| `occ/service.py:34 AUTO_SQUASH_MAX_DEPTH=100` | `eos-occ/src/service.rs:19` + `eos-layerstack/src/lib.rs:66` (`= 100`) |
| `occ/maintenance.py:48-60 after_publish_sync` | `eos-daemon/src/dispatcher.rs:1622-1684 run_auto_squash_maintenance` |

**Daemon hot path confirmed wired** (so the O(1)/O(delta)/fast-mount primitives
are on the real shell path, not dead code):
`eos-daemon/src/dispatcher.rs:897 acquire_snapshot` →
`:1070/1217 allocate_overlay_writable_dirs` → (kernel mount via overlay) →
`:929/1151 capture_upperdir_for_occ` → `:1507 publish_layer` →
`:1510/1622 run_auto_squash_maintenance`. Also `command.rs:738,867,1207,1306`.

---

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|---|---|---|---|---|---|
| 1 | Lower-dir O(1): layers shared read-only (CoW), no per-overlay full copy | **match** (with caveat D2) | none | `stack.py:108-135` (lease + existing `layer_paths`, no render) | `stack.rs:343-372` (`acquire_snapshot` maps `manifest.layers`→paths, no `project`) | Per-snapshot work is O(1) lease+path-list (no render). BUT Rust serializes it under the exclusive writer lock (D2) — Python did not. |
| 2 | Upper-dir O(n·delta): each op stores only its own delta in its own upperdir | **match** | none | `writable_dirs.py:46-52`; `capture.py:49-89` | `writable_dirs.rs:65-79` (per-`run_dir` `upper`/`work`); `path_change.rs:155-269` (walks only upperdir) | Per-op `run_dir/upper`; capture walks only upperdir → cost ∝ writes. |
| 3 | Fast: kernel overlayfs mount + manifest CAS pointer-swap, no deep per-op copy | **match** | none | `kernel_mount.py:49-75`; `publisher.py:49-138` | `kernel_mount.rs:106-137`; `stack.rs:618-681` | `lowerdir+` per layer newest-first; fd-backed lowerdirs; `move_mount` onto `workspace_root`; publish = stage→rename→CAS→prepend→atomic manifest. |
| 4 | Benchmarks exercise the Rust daemon (eosd) and measure these properties | **partial** | medium | `bench_rust_daemon_phase2.py:56,202-304`; `phase3.py:305-427` | (bench scripts target `eosd`; see below) | Targets eosd + concurrent load, BUT measures **latency + RSS only**; never asserts disk-space O(repo)+O(N·delta) vs O(N·repo). Phase 3 now builds a real workspace base through the daemon; the old phase2 baseline still uses a tiny one-file fixture. |

Supporting constant-parity checks (all **match**):

| Constant / operator | Python | Rust |
|---|---|---|
| `AUTO_SQUASH_MAX_DEPTH` | `occ/service.py:34` = `100` | `eos-occ/src/service.rs:19` = `100`; `eos-layerstack/src/lib.rs:66` = `100` |
| auto-squash trigger | `maintenance.py:50` `active.depth <= max_depth` → skip | `dispatcher.rs:1633` `active.depth() <= AUTO_SQUASH_MAX_DEPTH` → skip |
| plan: already-shallow | `squash.py:73` `<= max_depth` | `squash.rs:177` `<= max_depth` |
| plan: no fold | `squash.py:82` `len(entries) >= depth` | `squash.rs:182` `entries.len() >= layers.len()` |
| plan: min reduction | `squash.py:84` `depth - len(entries) < min_reduction` | `squash.rs:185` `< min_reduction` |
| plan: cap-unreachable | `squash.py:84-85` `len(entries) > max_depth and all(<= max_depth)` | `squash.rs:195-199` `> max_depth && all(<= max_depth)` |
| `min_reduction` defaults | squash `1`, `can_squash` `2` (`stack.py`) | squash `1` (`stack.rs:422`), `can_squash` `2` (`stack.rs:401`) |
| base layer id | `workspace_base.py:31` `B000001-base` | `workspace_base.rs:22` `B000001-base` |
| writable root | `writable_dirs.py:13` `/eos/mount` | `writable_dirs.rs:14` `/eos/mount` |
| lowerdir order / fd-pin | `kernel_mount.py:62-66,180` newest-first, `/proc/self/fd/` lowerdirs only | `kernel_mount.rs:111-113,236-238` newest-first, fd-paths lowerdirs only |

---

## Disparities

### D1 — Benchmarks prove latency/RSS, not the space-complexity property (Invariant 4 partial)
- Evidence: `bench_rust_daemon_phase3.py` gates on latency
  (`shell_noop_70pct_faster_than_phase1`, line 98) and daemon RSS
  (`sample_daemon_memory`, summarized lines 339-427); there is **no** `du`,
  on-disk byte accounting, or O(N·repo) regression check. Current Phase 3 builds
  the base through `api.build_workspace_base` against the target image workspace,
  which is a stronger fixture than the removed ad hoc tar seeding helper, but it
  still does not measure the disk-space invariant. The older phase2 baseline
  remains a one-file fixture: `bench_rust_daemon_phase2.py:61`
  `README_CONTENT = "# README\n…"`, `:297`
  `…/layers/B000001-base/README.md`.
- Why it matters: invariant 4 asks for a benchmark that *proves* the space
  properties. The current benches prove the daemon is fast and memory-stable
  under concurrency (real and useful), but the headline disk-space moat
  (`O(repo)+O(N·changed_bytes)`, not `O(N·repo)`) is unverified by any in-tree
  benchmark. The property holds **by construction** in the code (D-analysis
  above), but is not empirically guarded against regression.
- Suggested fix: add a bench/test that seeds a non-trivial base (e.g. MBs across
  many files), runs N concurrent unique-write ops, and asserts post-run on-disk
  size ≈ `base + Σ deltas` (and that no `layers/` dir holds a full copy of the
  base per op). A `tests/live_e2e_test` space-accounting case would close it.
- NOT a Rust regression: these are the same bench scripts used for the Python/CP-0
  baseline (`phase2.py:97-100` compares against `bench/baseline-amd64.json`). The
  missing space-accounting gate is a **pre-existing** gap shared with the Python
  baseline, not a check the Rust port dropped. "partial" here means "no benchmark
  proves the space property on either side", not "Rust lost a Python check".

### D2 — Rust `acquire_snapshot` used the exclusive storage-writer lock; source-level remediation landed
- Original evidence (Python, parent `a8c987845`): `acquire_snapshot` used the
  process-local `self._lock` around `read_active_manifest()` +
  `self._leases.acquire(...)` and did **not** enter `_storage_write_guard()`.
  Mutating methods (`release_lease`, `publish_changes`, `squash`,
  `commit_to_workspace`) used the heavier storage-writer guard.
- Original Rust gap: `LayerStack::acquire_snapshot` took
  `self.writer_lock.exclusive()`, the same in-process serializer used by
  `publish_layer`, `squash`, `release_lease`, and `commit_to_workspace`. Because
  daemon requests open fresh `LayerStack` instances per request, that per-root
  serializer was the real cross-request contention point.
- Remediation update: `StorageWriterLockLease` now exposes a shared read guard
  and a reentrant exclusive write guard. `LayerStack::acquire_snapshot` now takes
  `self.writer_lock.shared()?`; storage mutations keep `exclusive()?`. This
  restores the intended shape: concurrent snapshots can overlap, while publish,
  squash, release, and commit still serialize.
- Verification: `storage_lock::tests::shared_guards_overlap_and_block_exclusive`
  proves shared guards overlap and block a writer;
  `storage_lock::tests::exclusive_guard_is_reentrant_and_blocks_shared` proves
  writer re-entry still works and blocks readers until the outer write guard
  drops. The focused lock tests, full `eos-layerstack` lib tests, and scoped
  Clippy passed.
- Remaining follow-up: re-baseline the Phase 3/Phase 3T N-concurrent throughput
  matrix on a live Docker image. The source-level contention bug is closed; the
  measured throughput gate is not refreshed by this note.

### D3 — `acquire_snapshot` timings relocated from stack to daemon (divergent, equivalent)
- Evidence: Python `stack.py:108-135` records
  `timings={"layer_stack.acquire_snapshot.total_s": …}` **inside** the snapshot
  method. Rust `stack.rs:370` returns `timings: BTreeMap::new()` (empty). The
  metric is instead recorded at the **daemon** call site
  (`dispatcher.rs:971`, `:1247`; read back by the bench at `dispatcher.rs:3233`).
- Why it matters: behaviorally equivalent — the timing key still exists on the
  RPC response the bench reads. But a direct unit-level consumer of
  `LayerStack::acquire_snapshot` would see no timing. Low risk; flagged for
  completeness so a future reader does not mistake the empty map for a dropped
  metric.
- Suggested fix: none required. Optionally document that snapshot timing is owned
  by the daemon layer in Rust.

---

## Extra findings

- **No deep copy anywhere on the snapshot/mount path.** `MergedView::project`
  (`stack.rs:132-139`) — the only full-tree render — is called solely from
  `commit_to_workspace` (`stack.rs:540`), `build_checkpoint`/squash
  (`squash.rs:234`), and tests, never from `acquire_snapshot` or the overlay
  mount. This matches the Python "projection is a pinned list, not a rendered
  tree" invariant (layerstack.html 2.3 / `stack.py:105-129`).
- **CoW is delegated to the kernel, correctly.** The Rust port never copies
  lower layers; overlayfs copy-up happens in-kernel on first write into the
  private `upperdir`. The fd-pinning of lowerdirs (`kernel_mount.rs:236-238`)
  and the real-path upper/work mirror the Python rationale comment exactly.
- **Whiteout/opaque capture is O(writes), faithful.** `capture_upperdir`
  (`path_change.rs:155-269`) sorts per level, emits opaque-dir before children
  via `emitted_opaque_dirs` dedup, hashes content with SHA-256 — same shape as
  `capture.py`. No lower-layer scan.
- **Publish prepends newest-first + idempotent head short-circuit.**
  `stack.rs:626-628` returns early when the new changeset digest equals the head
  layer digest (matches publisher.py idempotent-duplicate short-circuit), saving
  a redundant layer — a real cost optimization preserved.
- **Auto-squash bounds read-amp on the real path.** `dispatcher.rs:1510` runs
  `run_auto_squash_maintenance` after a successful `publish_layer`, with the same
  `<= 100` gate, keeping per-path read cost (newest-first chain walk) and
  mount(8) layer count bounded — the cost-management half of the O(1)-snapshot
  trade.
- **Other bench scripts also target eosd**: `phase3` derives from `phase2`
  (`upload_artifact`, `call_tcp`), and `bench_rust_daemon_isolated_inspection.py`,
  `bench_rust_daemon_plugin.py`, `bench_rust_daemon_phase3t_*` all reuse the
  `eosd` upload harness (grep confirms `eosd` references in each).

---

## Open questions

1. Is there any intent to add a disk-space (du / byte-accounting) gate to the
   bench suite, or is the space property considered "proven by construction" and
   guarded only by the live-e2e capture/squash correctness suites? (D1.)
2. The shell-pre-mount squash default `64`
   (`EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH`, `pipeline.py:455-463`) lived in the
   now-deleted Python `ephemeral_workspace`. Does the Rust daemon implement an
   equivalent shell-pre-mount squash, or does it rely solely on the post-publish
   `AUTO_SQUASH_MAX_DEPTH=100` path? (Out of this area's core 4 invariants, but
   relevant to mount(8) depth-cap performance; not located in eos-daemon during
   this pass.)
3. (See D2.) Is Rust taking `writer_lock.exclusive()` inside `acquire_snapshot`
   intentional (atomicity of manifest-read + lease-acquire against a concurrent
   swap), or an over-tightening of the lock relative to Python's process-local
   `self._lock`? This determines whether D2 is a deliberate trade or a
   throughput regression to fix.
