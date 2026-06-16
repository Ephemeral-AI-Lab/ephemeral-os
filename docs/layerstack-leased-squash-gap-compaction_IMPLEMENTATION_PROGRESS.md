# LayerStack Leased-Layer Gap Compaction Implementation Progress

Date: 2026-06-16

Spec: `docs/layerstack-leased-squash-gap-compaction_SPEC.md`

Worktree:
`/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os-remount-experiment`

## Summary Verdict

Phases 1 through 6 are implemented and verified at the LayerStack level. Phase
1 proved the
protected-set planner shape. Phase 2 proved real on-disk reclaim for view-safe
intervals, including write-only intervals above protected fences and bottom
intervals without kept lower layers. Phase 3 proved boundary-preserving delta
checkpoints for deletes and opaque directories above protected lower content.
Phase 4 proved explicit copy-through accounting for hard active-depth guards.
Phase 5 proved command lease admission can acquire a bounded snapshot even while
a legacy lease still pins the old chain. Phase 6 proved the storage/refcount
shape for live-lease parent-prefix normalization: the lease remains active, the
parent prefix under `l4` is compacted and reclaimed, and the top unleased gap is
then reclaimable.

Verdict: the focused LayerStack feasibility implementation is complete. The next
work is daemon command-launch/remount integration and live
workspace-runtime-command E2E.

## Phase 1: Protected-Set Planner

Status: Complete.

### Files Changed

- `crates/daemon/layerstack/src/lease_aware.rs`
  - New planner module for leased-layer gap compaction.
- `crates/daemon/layerstack/src/lib.rs`
  - Exports the Phase 1 planner API.
- `crates/daemon/layerstack/tests/unit/lease_aware.rs`
  - Focused unit coverage for planner behavior.
- `crates/daemon/layerstack/examples/bench_layerstack_gap_planner.rs`
  - M1 planner experiment benchmark.

### Type Changes

- `LeaseAwareCheckpointMode`
  - Variants: `View`, `DeltaRequired`.
  - Method: `as_str()`.
  - Purpose: records whether a future checkpoint can be flattened or must
    preserve boundary deletes/opaque markers because kept lower layers exist.
- `ReclaimingInterval`
  - Fields: `layers: Vec<LayerRef>`,
    `checkpoint_mode: LeaseAwareCheckpointMode`.
  - Purpose: represents one maximal reclaimable unleased run.
- `LeaseAwarePlanEntry`
  - Variants: `KeepProtected(LayerRef)`, `KeepUnleased(LayerRef)`,
    `ReclaimingInterval(ReclaimingInterval)`.
  - Purpose: models protected fences, unleased layers too small to compact, and
    reclaimable unleased intervals.
- `LeaseAwarePlan`
  - Fields: `active_version`, `active_layer_count`, `protected_layer_count`,
    `kept_unleased_layer_count`, `reclaiming_interval_count`,
    `reclaiming_layer_count`, `entries`.
  - Methods: `active_depth_after_reclaiming_checkpoints()`,
    `has_reclaiming_intervals()`, `reclaiming_intervals()`.
  - Purpose: records planner shape and summary counts for policy/benchmark
    decisions.

### Method Changes

- Added `plan_lease_aware_gaps(active_manifest, protected_layers,
  min_reclaiming_interval_layers)`.
  - Computes the protected set from full layer refs supplied by the lease
    registry or test harness.
  - Scans active manifest order, newest-first.
  - Emits maximal reclaiming intervals for unleased runs with length greater
    than or equal to `min_reclaiming_interval_layers`.
  - Emits `KeepUnleased` for smaller unleased runs.
  - Marks intervals above kept lower layers as `DeltaRequired`; bottom intervals
    can use `View`.

### Unit Tests

- `fully_leased_stack_has_no_reclaiming_intervals`
- `unleased_prefix_compacts_above_protected_suffix`
- `same_file_gap_plans_around_single_protected_layer`
- `mounted_l4_lease_keeps_lower_prefix_until_normalized_or_released`
- `mounted_l4_lease_after_parent_normalization_keeps_compact_parent`
- `alternating_single_unleased_layers_are_kept_by_minimum_interval`

### M1 Experiment Results

Command:

```sh
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_planner
```

The benchmark executes each planner case 10,000 times and reports average
planner time per iteration. Byte columns are estimated with a 1 MiB same-file
rewrite payload per layer. `bytes_after_release` is the expected same-file
result after all protection is gone and a final squash runs.

| Case | Layers | Protected | Intervals | Before | After | After Release | Depth Before -> After | Avg Planner Time | Success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `fully_leased_50_same_file` | 50 | 50 | 0 | 50 MiB | 50 MiB | 1 MiB | 50 -> 50 | 11.216 us | true |
| `protected_suffix_50_unleased_prefix_10` | 60 | 50 | 1 | 60 MiB | 51 MiB | 1 MiB | 60 -> 51 | 12.183 us | true |
| `disjoint_historical_protected_versions` | 12 | 3 | 4 | 12 MiB | 7 MiB | 1 MiB | 12 -> 7 | 1.465 us | true |
| `alternating_single_unleased_layers` | 6 | 3 | 0 | 6 MiB | 6 MiB | 1 MiB | 6 -> 6 | 0.868 us | true |
| `single_protected_l4_gap_same_file` | 6 | 1 | 2 | 6 MiB | 3 MiB | 1 MiB | 6 -> 3 | 0.612 us | true |
| `mounted_l4_protects_lower_prefix_same_file` | 6 | 4 | 1 | 6 MiB | 5 MiB | 1 MiB | 6 -> 5 | 0.894 us | true |
| `mounted_l4_after_parent_normalization_same_file` | 4 | 2 | 1 | 4 MiB | 3 MiB | 1 MiB | 4 -> 3 | 0.500 us | true |

Important example verdict:

```text
oldest-to-newest: n1, n2, n3, l4, n5, n6
protected:        l4 only
planned active:   C(n6,n5), Keep(l4), C(n3,n2,n1)
estimated bytes:  6S -> 3S while l4 is protected -> 1S after l4 release
```

Current mounted-lease caveat:

```text
lease at l4 protects n1,n2,n3,l4 in today's lowerdir model
planned active: C(n6,n5), Keep(l4), Keep(n3), Keep(n2), Keep(n1)
estimated bytes: 6S -> 5S until that lease is normalized or released
```

After parent-prefix normalization:

```text
lease at l4 has been remounted/retargeted to l4,C(n3,n2,n1)
planned active: C(n6,n5), Keep(l4), Keep(C(n3,n2,n1))
estimated bytes: 4S normalized active -> 3S while l4 is protected -> 1S after l4 release
```

Phase 1 verdict: pass. The planner is fast relative to expected filesystem
checkpoint work and preserves the lease-boundary distinction the policy needs.

### Verification

Commands run:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack lease_aware
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_planner
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack
git diff --check
```

Results:

- `cargo fmt` passed.
- Focused `lease_aware` tests passed: 6/6.
- M1 planner benchmark passed: 7/7 rows reported `success=true`.
- Full `layerstack` package tests passed: 76 unit tests, 1 CAS fixture test,
  9 stack integration tests, and 0 doctests.
- `git diff --check` passed.

Not run:

- `cargo test -p operation --all-targets`, `xtask package`, and live
  workspace-runtime E2E were not run for Phase 1 because this change does not
  alter command finalization, daemon dispatch, mount behavior, or active squash
  execution.

## Phase 2: Reclaiming View Checkpoints

Status: Complete for view-safe intervals.

### Files Changed

- `crates/daemon/layerstack/src/lease_aware.rs`
  - Added `LeaseAwareReclaimOutcome`.
- `crates/daemon/layerstack/src/lib.rs`
  - Exports `LeaseAwareReclaimOutcome`.
- `crates/daemon/layerstack/src/stack/mod.rs`
  - Added the experimental reclaim method on `LayerStack`.
- `crates/daemon/layerstack/src/stack/lease_cleanup.rs`
  - Added shared cleanup helper for unreferenced candidate layers.
- `crates/daemon/layerstack/src/stack/projection.rs`
  - Added boundary-marker detection for view-checkpoint safety.
- `crates/daemon/layerstack/tests/stack.rs`
  - Added real storage tests for view-safe reclaim and delete-skip behavior.
- `crates/daemon/layerstack/examples/bench_layerstack_gap_reclaim.rs`
  - Added M2 real-storage benchmark.

### Type Changes

- `LeaseAwareReclaimOutcome`
  - Fields: `manifest`, `protected_layer_count`,
    `planned_reclaiming_interval_count`, `view_checkpoint_count`,
    `skipped_delta_interval_count`, `removed_layer_count`,
    `active_depth_before`, `active_depth_after`.
  - Purpose: reports what the experimental reclaim path actually committed or
    skipped.

### Method Changes

- Added `LayerStack::reclaim_lease_aware_view_checkpoints(
  min_reclaiming_interval_layers
  )`.
  - Acquires the LayerStack writer lock.
  - Reads the active manifest and full protected layer set from live lease
    refcounts.
  - Builds a `LeaseAwarePlan`.
  - Reclaims intervals that can safely use a flattened view checkpoint.
  - Treats `View` intervals as safe.
  - Treats `DeltaRequired` intervals as safe only if their layer directories
    contain no whiteouts or opaque markers.
  - Keeps unsafe delta-required intervals unchanged and increments
    `skipped_delta_interval_count`.
  - Rewrites the active manifest only when at least one view checkpoint is
    created.
  - Deletes old interval inputs through the same active-manifest and lease
    refcount check used by lease release.
- Added `projection::layer_has_boundary_markers(layer_dir)`.
  - Detects logical whiteouts, kernel whiteouts, and opaque markers.
- Added `lease_cleanup::remove_unreferenced_layer_candidates_locked(...)`.
  - Removes only candidates absent from the current active manifest and absent
    from live lease refcounts.

### Unit And Integration Tests

- `lease_aware_view_reclaim_compacts_same_file_gap_around_single_protected_layer`
  - Builds `n1, n2, n3, l4, n5, n6` as six same-file 1 MiB rewrites.
  - Retargets a test lease so only `l4` is protected.
  - Runs view-safe reclaim.
  - Asserts real layer payload is `6 MiB -> 3 MiB` while `l4` is protected.
  - Releases `l4`, runs final squash, and asserts payload becomes `1 MiB`.
- `lease_aware_view_reclaim_skips_delete_gap_until_delta_checkpoint`
  - Leased lower layer writes `a.txt`.
  - Unleased upper layer deletes `a.txt`.
  - Runs view-safe reclaim.
  - Asserts no checkpoint is created, no layer is removed, and the active view
    still reports `a.txt` absent.

### M2 Experiment Results

Command:

```sh
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

The benchmark reports real payload bytes under `storage_root/layers`, not
planner estimates.

| Case | Layers | Protected | Intervals | Before | After | Added | Removed | Pinned | After Release | Depth Before -> After | Time | Success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `single_protected_l4_same_file_view_reclaim` | 6 | 1 | 2 | 6 MiB | 3 MiB | 2 MiB | 5 MiB | 1 MiB | 1 MiB | 6 -> 3 | 0.010761417s | true |
| `mounted_l4_prefix_same_file_view_reclaim` | 6 | 4 | 1 | 6 MiB | 5 MiB | 1 MiB | 2 MiB | 4 MiB | 1 MiB | 6 -> 5 | 0.009312666s | true |
| `delete_above_protected_skipped_until_delta` | 2 | 1 | 1 | 1 MiB | 1 MiB | 0 MiB | 0 MiB | 1 MiB | 0 MiB | 2 -> 2 | 0.000052958s | true |

Important example verdict:

```text
oldest-to-newest: n1, n2, n3, l4, n5, n6
protected:        l4 only
real storage:     6S -> 3S while l4 is protected -> 1S after l4 release
```

Current mounted-lease verdict:

```text
lease at l4 protects n1,n2,n3,l4 in today's lowerdir model
real storage: 6S -> 5S while mounted lease is active -> 1S after release
```

Boundary verdict:

```text
delete above protected lower file is skipped in Phase 2
Phase 3 must implement delta checkpoint semantics before reclaiming it
```

### Verification

Commands run:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack lease_aware
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_planner
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack
git diff --check
```

Results:

- `cargo fmt` passed.
- Focused `lease_aware` and `lease_aware_view_reclaim` tests passed: 7/7
  matching tests.
- M1 planner benchmark passed: 7/7 rows reported `success=true`.
- M2 reclaim benchmark passed: 3/3 rows reported `success=true`.
- Full `layerstack` package tests passed: 76 unit tests, 1 CAS fixture test,
  11 stack integration tests, and 0 doctests.
- `git diff --check` passed.

Phase 2 verdict: pass. Proceed to Phase 3.

## Phase 3: Boundary-Preserving Delta Checkpoints

Status: Complete for delete and opaque boundary intervals.

### Files Changed

- `crates/daemon/layerstack/src/capture.rs`
  - Added internal unbounded stored-layer capture.
- `crates/daemon/layerstack/src/lease_aware.rs`
  - Added `delta_checkpoint_count` to `LeaseAwareReclaimOutcome`.
- `crates/daemon/layerstack/src/stack/mod.rs`
  - Added delta-enabled reclaim entry point and delta checkpoint builder.
- `crates/daemon/layerstack/tests/unit/capture.rs`
  - Updated internal capture helper call for the new max-file-size argument.
- `crates/daemon/layerstack/tests/stack.rs`
  - Added delete and opaque delta checkpoint integration tests.
- `crates/daemon/layerstack/examples/bench_layerstack_gap_reclaim.rs`
  - Added M3 benchmark rows.

### Type Changes

- `LeaseAwareReclaimOutcome`
  - Added `delta_checkpoint_count`.
  - Purpose: separates view checkpoints from boundary-preserving delta
    checkpoints in benchmark and policy reporting.

### Method Changes

- Added `capture::capture_layer_dir_unbounded(layer_dir)`.
  - Reuses the existing whiteout/opaque/symlink/file capture logic.
  - Removes the command upperdir 8 MiB file-size cap for stored layer parsing.
- Added `LayerStack::reclaim_lease_aware_checkpoints(...)`.
  - Runs the same lease-aware planner as Phase 2.
  - Builds view checkpoints for view-safe intervals.
  - Builds delta checkpoints for intervals that require boundary preservation.
- Kept `LayerStack::reclaim_lease_aware_view_checkpoints(...)`.
  - Calls the same internal implementation with delta checkpoints disabled.
  - Preserves Phase 2 benchmark coverage.
- Added internal delta helpers:
  - `build_delta_checkpoint`.
  - `delta_changes_for_interval`.
  - `apply_delta_change`.
  - `is_same_or_descendant`.
  - `is_strict_descendant`.
- Delta reducer behavior:
  - Processes interval layers oldest-to-newest.
  - Keeps newest operation per exact path.
  - Preserves deletes as whiteouts.
  - Preserves opaque directory markers.
  - Drops older descendant operations when a newer opaque/delete/write replaces
    a directory path.

### Unit And Integration Tests

- `lease_aware_delta_reclaim_preserves_delete_above_protected_lower_file`
  - Protected lower layer writes `a.txt`.
  - Unleased upper layer deletes `a.txt`.
  - Delta reclaim replaces the delete layer with a delta checkpoint.
  - Active view still reports `a.txt` absent while the protected lower file is
    leased.
  - After lease release and final squash, retained payload becomes 0.
- `lease_aware_delta_reclaim_preserves_opaque_dir_above_protected_lower_entries`
  - Protected lower layer writes `dir/protected.txt`.
  - Older unleased layer writes `dir/old-unleased.txt`.
  - Newer unleased layer marks `dir` opaque.
  - Delta reclaim replaces the unleased interval with an opaque delta
    checkpoint.
  - Active view keeps both lower descendants hidden.

### M3 Experiment Results

Command:

```sh
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

| Case | Layers | Protected | Intervals | Before | After | Added | Removed | Pinned | After Release | Depth Before -> After | Time | Success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `delete_above_protected_delta_checkpoint` | 2 | 1 | 1 | 1 MiB | 1 MiB | 0 MiB | 0 MiB | 1 MiB | 0 MiB | 2 -> 2 | 0.026220708s | true |
| `opaque_above_protected_delta_checkpoint` | 3 | 1 | 1 | 2 MiB | 1 MiB | 0 MiB | 1 MiB | 1 MiB | 0 MiB | 3 -> 2 | 0.026289000s | true |

Boundary verdict:

```text
delete above protected lower file stays deleted after compaction
opaque directory above protected lower descendants keeps descendants hidden
```

### Verification

Commands run:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack lease_aware_delta
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

Results:

- `cargo fmt` passed.
- Focused delta tests passed: 2/2.
- M3 benchmark rows passed: 2/2 rows reported `success=true`.

Phase 3 verdict: pass. Proceed to Phase 4.

## Phase 4: Copy-Through Accounting

Status: Complete for explicit active-depth guard copy-through.

### Files Changed

- `crates/daemon/layerstack/src/lease_aware.rs`
  - Added `LeaseAwareCopyThroughOutcome`.
- `crates/daemon/layerstack/src/lib.rs`
  - Exports `LeaseAwareCopyThroughOutcome`.
- `crates/daemon/layerstack/src/stack/mod.rs`
  - Added explicit copy-through method and accounting helpers.
- `crates/daemon/layerstack/tests/stack.rs`
  - Added copy-through storage/accounting integration test.
- `crates/daemon/layerstack/examples/bench_layerstack_gap_reclaim.rs`
  - Added M4 benchmark row.

### Type Changes

- `LeaseAwareCopyThroughOutcome`
  - Fields: `manifest`, `protected_layer_count`, `checkpoint_count`,
    `removed_layer_count`, `bytes_added`, `protected_pinned_bytes`,
    `active_depth_before`, `active_depth_after`.
  - Purpose: reports non-reclaiming copy-through cost separately from reclaim.

### Method Changes

- Added `LayerStack::copy_through_active_for_depth_guard(max_depth)`.
  - No-ops when active depth is already within the guard.
  - Projects the full active manifest into one checkpoint when depth exceeds
    the guard.
  - Rewrites active manifest to the new checkpoint.
  - Removes only old unleased inputs; protected inputs remain pinned.
  - Reports `bytes_added` and `protected_pinned_bytes`.
- Added internal helpers:
  - `build_copy_through_checkpoint`.
  - `layer_payload_sum`.

### Integration Test

- `lease_aware_copy_through_reports_pinned_bytes_without_reclaiming_protected_layers`
  - Fully protected six-layer same-file stack.
  - Copy-through with depth guard 1.
  - Asserts active depth becomes `6 -> 1`.
  - Asserts storage grows `6 MiB -> 7 MiB` while lease is held.
  - Asserts `bytes_added = 1 MiB`, `protected_pinned_bytes = 6 MiB`, and
    `removed_layer_count = 0`.
  - After lease release, cleanup returns storage to `1 MiB`.

### M4 Experiment Results

Command:

```sh
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

| Case | Layers | Protected | Before | After | Added | Removed | Pinned | After Release | Depth Before -> After | Time | Success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `copy_through_fully_protected_depth_guard` | 6 | 6 | 6 MiB | 7 MiB | 1 MiB | 0 MiB | 6 MiB | 1 MiB | 6 -> 1 | 0.026716791s | true |

Copy-through verdict:

```text
copy-through bounds active depth but is not reclaim while protected layers are leased
metrics explicitly report added checkpoint bytes and protected pinned bytes
```

### Verification

Commands run:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack lease_aware_copy_through
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

Results:

- `cargo fmt` passed.
- Focused copy-through test passed: 1/1.
- M4 benchmark row passed: 1/1 row reported `success=true`.

Phase 4 verdict: pass. Proceed to Phase 5.

## Phase 5: Command Lease Admission Smoke

Status: Complete at LayerStack simulation level.

### Files Changed

- `crates/daemon/layerstack/src/stack/mod.rs`
  - Added bounded command snapshot acquisition.
- `crates/daemon/layerstack/src/lib.rs`
  - Exports `BoundedCommandSnapshot`.
- `crates/daemon/layerstack/tests/stack.rs`
  - Added command admission smoke test.
- `crates/daemon/layerstack/examples/bench_layerstack_gap_reclaim.rs`
  - Added M5 benchmark row.

### Type Changes

- `BoundedCommandSnapshot`
  - Fields: `lease`, `copy_through`.
  - Purpose: returns the command lease plus the admission-time copy-through
    accounting used to bound the command's starting generation.

### Method Changes

- Added `LayerStack::acquire_bounded_snapshot_for_command(
  owner_request_id,
  max_depth
  )`.
  - Runs `copy_through_active_for_depth_guard(max_depth)` first.
  - Acquires the command snapshot after active depth is bounded.
  - Does not remount or retarget any already-running lease.

### Integration Test

- `lease_aware_command_admission_acquires_bounded_snapshot_despite_legacy_lease`
  - Builds six same-file 1 MiB layers.
  - Acquires a legacy lease over the six-layer chain.
  - Runs bounded command admission with depth guard 1.
  - Asserts the new command lease starts from depth 1.
  - Asserts active depth becomes 1.
  - Asserts storage grows `6 MiB -> 7 MiB` while the legacy lease is active.
  - Releasing the new command lease does not reclaim the old chain.
  - Releasing the legacy lease returns storage to `1 MiB`.

### M5 Experiment Results

Command:

```sh
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

| Case | Layers | Protected | Before | After | Added | Removed | Pinned | After Release | Depth Before -> After | Time | Success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `command_admission_bounded_snapshot_with_legacy_lease` | 6 | 6 | 6 MiB | 7 MiB | 1 MiB | 0 MiB | 6 MiB | 1 MiB | 6 -> 1 | 0.026729000s | true |

Command admission verdict:

```text
new command lease starts from bounded depth-1 generation
legacy running lease still pins old bytes until release
```

### Verification

Commands run:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack lease_aware_command_admission
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

Results:

- `cargo fmt` passed.
- Focused command admission smoke test passed: 1/1.
- M5 benchmark row passed: 1/1 row reported `success=true`.

Phase 5 verdict: pass at LayerStack simulation level. Daemon command launch
integration and live workspace-runtime-command E2E remain future work.

## Phase 6: Live Lease Parent-Prefix Normalization

Status: Complete at LayerStack storage/refcount simulation level.

### Files Changed

- `crates/daemon/layerstack/src/lease_aware.rs`
  - Added `LeaseParentCompactionOutcome`.
- `crates/daemon/layerstack/src/lib.rs`
  - Exports `LeaseParentCompactionOutcome`.
- `crates/daemon/layerstack/src/stack/leases.rs`
  - Added lease-manifest lookup for normalization planning.
- `crates/daemon/layerstack/src/stack/mod.rs`
  - Added parent-prefix compaction and active-manifest rewrite.
- `crates/daemon/layerstack/tests/unit/lease_aware.rs`
  - Added planner coverage for the post-normalization mounted-lease shape.
- `crates/daemon/layerstack/tests/stack.rs`
  - Added real storage coverage for live `l4` lease parent-prefix reclaim.
- `crates/daemon/layerstack/examples/bench_layerstack_gap_planner.rs`
  - Added post-normalization planner row.
- `crates/daemon/layerstack/examples/bench_layerstack_gap_reclaim.rs`
  - Added M6 real-storage benchmark row.

### Type Changes

- `LeaseParentCompactionOutcome`
  - Fields: `lease_manifest`, `active_manifest`, `compact_parent_layer`,
    `compacted_parent_layer_count`, `removed_layer_count`, `bytes_added`,
    `lease_depth_before`, `lease_depth_after`, `active_depth_before`,
    `active_depth_after`.
  - Purpose: reports the cost and effect of replacing a live lease's lower
    parent prefix with one compact parent checkpoint.

### Method Changes

- Added `LayerStack::compact_leased_parent_for_remount(lease_id,
  min_parent_layers)`.
  - Requires the live lease manifest to appear as a contiguous active-manifest
    suffix.
  - Builds one compact checkpoint for the lease's lower parent layers.
  - Rewrites the active suffix from `[head, parent...]` to
    `[head, compact_parent]`.
  - Retargets the lease from `[head, parent...]` to `[head, compact_parent]`.
  - Deletes old parent layers only after they are absent from active manifest
    and lease refcounts.
  - This models the LayerStack side of a production protocol where the daemon
    must remount the running command onto `[head, compact_parent]` before lease
    retarget and GC.
- Added internal `find_layer_sequence(...)`.
  - Finds the contiguous active-manifest interval represented by the live lease.
- Added `LeaseRegistry::manifest(lease_id)`.
  - Provides a clone of the current lease manifest for normalization planning.

### Unit And Integration Tests

- `mounted_l4_lease_after_parent_normalization_keeps_compact_parent`
  - Planner sees active `[n6,n5,l4,C(n3..n1)]` and protected
    `[l4,C(n3..n1)]`.
  - Asserts only `n6,n5` remain reclaimable and active depth becomes 3.
- `lease_aware_parent_prefix_compaction_keeps_live_l4_lease_but_reclaims_prefix`
  - Builds `n1,n2,n3,l4`, acquires the live command lease at `l4`, then
    publishes `n5,n6`.
  - Normalizes the lease parent prefix to `[l4,C(n3,n2,n1)]` while the lease
    remains active.
  - Asserts old parent layers `n3,n2,n1` are deleted.
  - Asserts the lease view still reads the `l4` snapshot and active view reads
    `n6`.
  - Reclaims `n5,n6` while the lease is still active.
  - Asserts storage is `6 MiB -> 4 MiB -> 3 MiB -> 1 MiB after release/final
    squash`.

### M6 Experiment Results

Command:

```sh
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

| Case | Layers | Protected | Intervals | Before | After | Added | Removed | Pinned | After Release | Depth Before -> After | Time | Success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `mounted_l4_prefix_normalized_live_lease_reclaim` | 6 | 2 | 1 | 6 MiB | 3 MiB | 2 MiB | 5 MiB | 2 MiB | 1 MiB | 6 -> 3 | 0.036328584s | true |

Live lease normalization verdict:

```text
lease remains active
parent prefix n1..n3 becomes C(n3,n2,n1)
top gap n5,n6 becomes C(n6,n5)
same-file storage reaches 3S while l4 is still leased, then 1S after release
```

### Verification

Commands run:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack mounted_l4_lease_after_parent_normalization_keeps_compact_parent
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack lease_aware_parent_prefix_compaction_keeps_live_l4_lease_but_reclaims_prefix
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

Results:

- `cargo fmt` passed.
- Focused post-normalization planner test passed: 1/1.
- Focused live lease parent-prefix storage test passed: 1/1.
- M6 benchmark row passed: 1/1 row reported `success=true`.

Phase 6 verdict: pass at LayerStack simulation level. Daemon integration must
still prove the running command mount is remounted before old lowerdir paths are
deleted.

## Final Verification

Commands run:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_planner
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack
git diff --check
```

Results:

- `cargo fmt` passed.
- M1 planner benchmark passed: 7/7 rows reported `success=true`.
- M2-M6 reclaim/copy-through/admission benchmark passed: 8/8 rows reported
  `success=true`.
- Full `layerstack` package tests passed: 77 unit tests, 1 CAS fixture test,
  16 stack integration tests, and 0 doctests.
- `git diff --check` passed.
