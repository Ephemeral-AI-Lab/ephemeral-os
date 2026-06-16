# LayerStack Leased-Layer Gap Compaction Spec

Date: 2026-06-16

## 1. Purpose

This focused spec handles the case where LayerStack squash runs while one or
more layers are leased. The goal is to avoid two bad outcomes:

1. A leased layer blocks unrelated unleased layers from being compacted.
2. Squash creates extra checkpoints for layers that are still leased, increasing
   storage without reclaiming anything.

Policy:

```text
Leased layers are immutable fences. Unleased gaps around those fences remain
eligible for reclaiming compaction.
```

## 2. Problem

If the planner only treats lease heads as boundaries, it can fold layers that
are still referenced by the same live lease.

Example, newest first:

```text
active: [P50, P49, P48, ..., P1]
lease:  [P50, P49, P48, ..., P1]
```

A lease-head-only planner keeps `P50` but may checkpoint `[P49..P1]`.
That produces:

```text
active after squash: [P50, Btail]
lease still pins:    [P50, P49, P48, ..., P1]
```

Storage increases by `Btail`, while the old tail cannot be deleted.

The second failure mode is over-blocking:

```text
active: [N10, N9, ..., N1, P50, P49, ..., P1]
lease:                 [P50, P49, ..., P1]
```

The leased suffix must stay, but `[N10..N1]` is not leased and should still be
compactable and reclaimable.

## 3. Goals

1. Compact unleased layers even when other active layers are leased.
2. Never checkpoint leased layers merely to reduce depth if the old leased
   inputs cannot be reclaimed.
3. Preserve correctness for deletes, whiteouts, opaque directories, symlinks,
   and normal file writes across leased boundaries.
4. Report bytes reclaimed, bytes still lease-blocked, and bytes added by any
   non-reclaiming copy-through checkpoint.
5. Keep active head growth bounded for future commands when doing so is worth
   explicit copy-through cost.

## 4. Non-Goals

1. Do not delete or rewrite a layer that a live lease still references.
2. Do not delete a mounted lowerdir path before the live session has either
   been remounted to a compact equivalent or a Linux mount experiment proves the
   old path can be unlinked without affecting the running overlay.
3. Do not solve unlimited historical session retention. This spec only makes
   unleased parts of the active chain compactable while leases exist.
4. Do not implement the daemon process-freeze/remount protocol in the first
   LayerStack-only experiment.
5. Do not require global content-addressed dedup in the first implementation.

## 5. Terminology

| Term | Definition |
| --- | --- |
| Protected layer | A `LayerRef` with live lease refcount greater than zero. |
| Unleased layer | A `LayerRef` with zero live lease refcount. |
| Fence | A protected layer kept as a hard interval boundary. |
| Unleased interval | A maximal contiguous run of unleased layers in active manifest order. |
| Reclaiming checkpoint | A checkpoint replacing only unleased layers, allowing old inputs to be deleted after commit. |
| Copy-through checkpoint | A checkpoint that reads protected layers to bound active depth, but cannot delete those protected inputs. |
| Boundary-preserving checkpoint | A checkpoint that preserves whiteouts and opaque markers needed to affect lower kept layers. |
| Parent-prefix normalization | A live-lease remount protocol that replaces the lower parent layers of a protected lease head with one compact checkpoint, then retargets the lease to that compact parent. |

LayerStack manifests are newest-first. A layer above another layer can hide or
delete paths from layers below it.

## 6. Core Design

### 6.1 Protected Set

Before planning squash, compute protected layers from the lease registry:

```text
protected = union(layer refs from every live lease manifest)
```

Do not use only lease heads. A lease over `[P50..P1]` protects every `P*`
layer, not only `P50`.

For a mounted command lease, this full-manifest protection is the starting
state, not the end state. If the session is remounted from `[P50..P1]` to
`[P50, C(P49..P1)]`, then the protected set changes to `[P50, C(P49..P1)]`
and the old physical parent layers become reclaimable.

### 6.2 Interval Planning

Scan the active manifest in order and split it into runs:

```text
active: [N10, N9, N8, P50, P49, U3, U2, P20, P19]

segments:
  unleased interval [N10, N9, N8]
  fence P50
  fence P49
  unleased interval [U3, U2]
  fence P20
  fence P19
```

Only unleased intervals are eligible for reclaiming checkpoints.

Planner output should include:

```rust
enum LeaseAwarePlanEntry {
    KeepProtected(LayerRef),
    KeepUnleased(LayerRef),
    ReclaimingInterval(Vec<LayerRef>),
    CopyThroughInterval(Vec<LayerRef>),
}
```

`KeepUnleased` is used when an unleased interval is too small to justify
compaction.

### 6.3 Reclaiming Checkpoint Rule

For each `ReclaimingInterval`:

1. Build a semantically equivalent checkpoint.
2. Replace that interval in the active manifest with the checkpoint.
3. After the manifest rewrite commits, delete old interval layers because they
   have zero lease refcount and are no longer active.

Common case:

```text
before active: [N10..N1, P50..P1]
protected:               [P50..P1]

after active:  [Bnew, P50..P1]
deleted:       [N10..N1]
```

### 6.4 Live Lease Parent-Prefix Normalization

A lease should be treated as evidence of a running command session. Therefore
the planner must not simply remove lower prefix directories that the current
mount may still name.

To reclaim the lower parent prefix of a protected layer while the command keeps
running, use an explicit normalization protocol:

1. Build a compact parent checkpoint from the lease's lower parent layers.
2. Remount the live session so its lower chain uses the protected head plus the
   compact parent checkpoint.
3. Verify the live mount now references the compact parent.
4. Retarget the lease manifest to the compact parent.
5. Rewrite the active manifest to use the same compact parent for the matching
   active interval.
6. GC old parent layers only after they are absent from both active manifest and
   lease refcounts.

Example, oldest-to-newest:

```text
active before: [n6, n5, l4, n3, n2, n1]
live lease:    [l4, n3, n2, n1]
```

Normalize the live lease parent:

```text
compact parent: C(n3,n2,n1)
live lease:     [l4, C(n3,n2,n1)]
active:         [n6, n5, l4, C(n3,n2,n1)]
deleted:        n3,n2,n1
```

Then ordinary gap reclaim can compact the top interval:

```text
active:  [C(n6,n5), l4, C(n3,n2,n1)]
storage: 3S for same-file S-byte rewrites while l4 remains leased
```

When `l4` is later released, a final squash can collapse the same-file chain to
`1S`.

## 7. Boundary Preservation

Flattened projection is not safe for every unleased interval.

Unsafe case:

```text
before active: [delete a.txt, Pbase(a.txt)]
protected:                   [Pbase(a.txt)]
```

If `[delete a.txt]` is projected into an empty checkpoint directory, the
checkpoint is empty. Then `a.txt` reappears from `Pbase`.

Therefore the planner must choose a checkpoint mode:

| Mode | Use When | Behavior |
| --- | --- | --- |
| View checkpoint | The interval has no kept lower layers whose content can be affected by interval deletes or opaque dirs. | Project merged interval view into a checkpoint directory. |
| Delta checkpoint | The interval has lower kept/protected layers below it. | Preserve writes, symlinks, whiteouts, and opaque markers as layer operations. |

Safe v1 default:

```text
if there is any kept layer below the interval:
    use delta checkpoint
else:
    view checkpoint is allowed
```

If delta checkpoint support is not implemented, skip that interval and report
`boundary_preservation_required`.

## 8. Delta Checkpoint Semantics

A delta checkpoint represents the net effect of an interval as a layer, not as
a fully flattened workspace view.

Required behavior:

1. Reads after replacing the interval must match reads before replacement.
2. Newest operation for each path wins inside the interval.
3. File writes remain file writes.
4. Symlink writes remain symlink writes.
5. Deletes remain whiteouts, even if the deleted path exists only in a lower
   protected layer.
6. Opaque directories remain opaque markers.
7. Entries below an opaque marker are included only if visible above that
   marker inside the interval.

Implementation options:

1. Parse layer directories back into ordered `LayerChange` values and aggregate
   them with whiteout/opaque awareness.
2. Add a lower-bound-aware projection mode that preserves boundary tombstones
   instead of deleting them during projection.

Option 1 is preferable because it reuses LayerStack's layer-change model and
makes invalid states easier to test.

## 9. Copy-Through Is Separate

Sometimes the active manifest must be bounded even though a protected suffix
cannot be deleted. In that case the system may build a copy-through checkpoint:

```text
before active: [Bnew, P50..P1]
protected:           [P50..P1]

after active:  [Bactive]
lease keeps:   [P50..P1]
```

This improves active depth for future mounts, but it does not reclaim protected
layers and it adds checkpoint bytes.

Copy-through is allowed only when one of these is true:

1. active depth exceeds a hard mount/depth guard,
2. future command launch would be blocked without it,
3. the checkpoint can be deduplicated/shared with an existing compact base,
4. an explicit operator/admin compaction request accepts the temporary storage
   cost.

Copy-through must report:

- bytes added,
- protected bytes still pinned,
- active depth before and after,
- leases preventing reclaim,
- expected reclaim opportunity after lease release.

## 10. Reclaim Algorithm

Squash commit flow:

1. Acquire the LayerStack writer lock.
2. Read the active manifest.
3. Snapshot the lease registry refcounts.
4. Build the protected set.
5. Plan unleased intervals and optional copy-through intervals.
6. Build checkpoints in staging.
7. Re-read active manifest and validate the planned active manifest still
   matches.
8. Write the new manifest atomically.
9. Release temporary squash lease.
10. Delete old layers only when:
    - not referenced by the new active manifest,
    - and lease refcount is zero.
11. Emit compaction/reclaim metrics.

Failure behavior:

- If checkpoint build fails before manifest write, delete staging and keep the
  old manifest.
- If active manifest changed before commit, discard checkpoints and retry later.
- If GC fails after manifest write, report reclaim failure but keep manifest
  valid.

## 11. Metrics

Required fields:

| Field | Meaning |
| --- | --- |
| `protected_layer_count` | Count of active layer refs with live lease refcount. |
| `unleased_interval_count` | Number of eligible unleased intervals. |
| `reclaiming_checkpoint_count` | Checkpoints that replace only unleased layers. |
| `copy_through_checkpoint_count` | Checkpoints that read protected layers and may add storage. |
| `bytes_reclaimable_before` | Bytes in planned unleased intervals. |
| `bytes_removed_after` | Bytes actually removed by GC. |
| `bytes_protected_pinned` | Bytes still pinned by live leases. |
| `bytes_added_copy_through` | Bytes added by copy-through checkpoints. |
| `bytes_added_parent_prefix` | Bytes added by compacting a live lease's lower parent prefix. |
| `parent_prefix_layers_removed` | Old parent-prefix layers reclaimed after remount and lease retarget. |
| `boundary_preservation_skipped_count` | Intervals skipped because delta checkpoint support was required. |

Trace events:

| Event | Purpose |
| --- | --- |
| `lease_gap_compaction_planned` | Reports protected set, intervals, reclaimable bytes, and copy-through candidates. |
| `lease_gap_compaction_finished` | Reports checkpoints built, bytes removed, bytes pinned, and skipped intervals. |
| `boundary_preservation_required` | Reports interval skipped because flattened projection would be unsafe. |
| `copy_through_checkpoint_created` | Reports non-reclaiming checkpoint cost and reason. |
| `lease_parent_prefix_normalized` | Reports compact parent checkpoint creation, remount/retarget completion, and reclaimed old parent layers. |

## 12. Examples

### Example A: New Layers Above Leased Suffix

```text
before active: [N3, N2, N1, P3, P2, P1]
lease:                 [P3, P2, P1]
```

Plan:

```text
ReclaimingInterval([N3, N2, N1])
KeepProtected(P3)
KeepProtected(P2)
KeepProtected(P1)
```

After:

```text
active: [Bnew, P3, P2, P1]
deleted: N3, N2, N1
```

### Example B: Delete Above Leased Base

```text
before active: [D(a.txt), Pbase(a.txt)]
lease:                    [Pbase(a.txt)]
```

Plan must use a delta checkpoint:

```text
active: [Bdelta(whiteout a.txt), Pbase(a.txt)]
read a.txt: absent
```

A view checkpoint is forbidden because it would make `a.txt` visible again.

### Example C: Fully Leased Stack

```text
before active: [P50, P49, ..., P1]
lease:         [P50, P49, ..., P1]
```

Reclaiming plan:

```text
KeepProtected(P50)
KeepProtected(P49)
...
KeepProtected(P1)
```

No checkpoint is created by default. Creating `Btail` would add storage without
reclaim.

### Example D: Hard Active Depth Guard

```text
before active: [Bnew, P50, P49, ..., P1]
lease:               [P50, P49, ..., P1]
```

If active depth must be bounded for future mounts, a copy-through checkpoint is
allowed:

```text
active: [Bactive]
lease keeps: [P50, P49, ..., P1]
```

This is not reclaim. Metrics must show protected bytes still pinned.

### Example E: Live Lease At `l4`, Parent Prefix Normalized

Oldest-to-newest same-file rewrites:

```text
n1, n2, n3, l4, n5, n6
```

Current active and lease manifests are newest-first:

```text
active: [n6, n5, l4, n3, n2, n1]
lease:  [l4, n3, n2, n1]
```

The lease implies a running command session. First normalize/remount that
session:

```text
lease after remount: [l4, C(n3,n2,n1)]
active after sync:   [n6, n5, l4, C(n3,n2,n1)]
```

Then gap reclaim compacts the top interval:

```text
active after reclaim: [C(n6,n5), l4, C(n3,n2,n1)]
same-file storage:    6S -> 3S while l4 is still leased
after l4 release:     1S after final squash
```

Without the normalization/remount step, the live lease still protects
`n3,n2,n1,l4`, so reclaim may only compact `n5,n6` and storage remains about
`5S`.

## 13. Implementation Phases

Every phase must land with a feasibility experiment before the implementation is
treated as policy-ready. The experiment should prove both correctness and the
space/time shape of the phase, even if the first version is planner-only.

### Phase 1: Planner Only

- Add protected-set planning from lease refcounts.
- Add tests for interval splitting.
- Ensure fully leased stacks do not create reclaiming checkpoints.
- Experiment gate: run planner-only synthetic cases with fully leased stacks,
  leased suffixes, unleased prefixes, and disjoint historical leases. The output
  must show no reclaiming interval contains a protected layer.

### Phase 2: Reclaiming View Checkpoints

- Compact unleased intervals that are bottom-closed or have no boundary deletes.
- Delete old unleased input layers after manifest rewrite.
- Report skipped boundary-sensitive intervals.
- Experiment gate: use real LayerStack temp roots to compact write-only
  unleased intervals above protected suffixes. The output must show input layers
  deleted, protected layers unchanged, and payload reduction roughly equal to
  reclaimed overwritten bytes minus the new checkpoint payload.

### Phase 3: Delta Checkpoints

- Parse layer directories into `LayerChange` values.
- Preserve deletes and opaque markers.
- Add regression tests for deletes above leased lower files.
- Experiment gate: compact unleased intervals containing whiteouts and opaque
  directories above leased lower content. The active view must preserve hidden
  descendants, while the lease view must still expose the protected older files.

### Phase 4: Copy-Through Accounting

- Add explicit copy-through mode for hard active-depth guards.
- Report added bytes and protected pinned bytes.
- Keep copy-through disabled by default unless a guard or explicit request
  requires it.
- Experiment gate: force a hard active-depth guard while a protected suffix is
  leased. The active depth must become bounded, protected layers must remain
  pinned, and metrics must report bytes added separately from bytes reclaimed.

### Phase 5: Command Lease Admission Smoke

- Bound the starting generation for new command leases before acquisition.
- Keep the already-running legacy lease unchanged.
- Report copy-through bytes separately from reclaimed bytes.
- Experiment gate: acquire a new command snapshot while a legacy lease pins the
  old active chain. The new command must start from bounded depth, and the
  benchmark must show the legacy lease still pins the old bytes until release.

### Phase 6: Live Lease Parent-Prefix Normalization

- Treat a lease as a running command session.
- Build one compact checkpoint for the lease's lower parent prefix.
- Remount the live session onto `[protected_head, compact_parent]` before
  retargeting the lease.
- Retarget the lease to the compact parent and rewrite the matching active
  suffix to reuse that same compact parent.
- Run ordinary gap reclaim above the protected head.
- Experiment gate: for `n1,n2,n3,l4,n5,n6`, acquire the live lease at `l4`,
  keep that lease active, normalize `n1..n3` into one compact parent, then
  compact `n5,n6`. The output must show `6S -> 3S` while `l4` remains leased,
  then `1S` after release and final squash.

## 14. Milestone Experiment Gates

These gates are intentionally small and repeatable. Each gate should produce a
machine-readable row and a human-readable summary. Rows can be emitted by
`crates/daemon/layerstack/examples/bench_layerstack.rs` or by a follow-up
specialized example if the main benchmark becomes too broad.

Recommended row schema:

```text
milestone,case,layers,protected_layers,lease_count,unleased_intervals,\
bytes_before,bytes_after,bytes_removed,bytes_added,pinned_bytes,\
active_depth_before,active_depth_after,bytes_after_release,duration_s,\
success,notes
```

### M0: Baseline Retention

- Purpose: keep the existing space/time baseline visible before changing
  policy.
- Cases:
  - `same_file_50x_1mib`
  - `multi_file_5000x_1kib`
  - `large_file_4x_64mib`
  - `current_plus_historical_leases`
- Success criteria:
  - Results continue to show retained edits scale with retained layer payload.
  - Results continue to separate duplicate current leases from historical
    version leases.
  - Output includes peak payload and duration where the benchmark can sample
    them.

### M1: Protected-Set Planner

- Purpose: prove the planner can find reclaimable gaps without touching the
  filesystem.
- Cases:
  - Fully leased 50-layer stack.
  - 50 protected older layers plus 10 newer unleased layers.
  - Multiple historical leases pinning disjoint versions.
  - Alternating protected and unprotected segments.
- Success criteria:
  - A fully leased stack yields zero reclaiming intervals.
  - No reclaiming interval includes a protected layer.
  - Unleased intervals are maximal and deterministic.
  - Planner output reports protected count, interval count, and skip reasons.

### M2: Reclaiming View Checkpoint

- Purpose: prove safe reclaim when the interval has no boundary-sensitive
  deletes or opaque markers.
- Cases:
  - 50 protected same-file rewrites plus 10 newer unleased same-file rewrites.
  - 50 protected layers plus 10 newer unleased layers touching several files.
  - 5 current leases to the same protected suffix.
  - Oldest-to-newest same-file rewrite chain
    `n1, n2, n3, l4, n5, n6`, where only `l4` is protected. The reclaiming
    plan must become `C(n1..n3), Keep(l4), C(n5..n6)`.
  - The same chain with a mounted lease at version `l4` in the current lowerdir
    model. That lease protects `n1..n3,l4`, so only `n5,n6` may compact until
    the lease is normalized or released.
- Success criteria:
  - The newer unleased interval collapses to one checkpoint.
  - Old unleased input layer directories are removed after manifest rewrite.
  - Protected lease reads are unchanged.
  - Active reads match the pre-compaction active projection.
  - Metrics show positive `bytes_removed` and no hidden copy-through bytes.
  - In the single-protected-layer case, six same-file `S`-byte rewrites retain
    about `3S` while `l4` is leased: one checkpoint for `n1..n3`, one protected
    `l4` payload, and one checkpoint for `n5..n6`.
  - After releasing `l4`, the next cleanup/squash collapses the same-file chain
    to about `1S` and records that value in `bytes_after_release`.
  - In the mounted-`l4` current-model case, the experiment must not claim `3S`
    unless `n1..n3,l4` were first normalized into one compact protected base.

### M3: Boundary-Preserving Delta Checkpoint

- Purpose: prove correctness when unleased layers contain deletes or opaque
  directory markers above protected lower content.
- Cases:
  - Protected base contains `a.txt`; unleased interval deletes `a.txt`.
  - Protected base contains `dir/lower.txt`; unleased interval marks `dir`
    opaque and writes `dir/new.txt`.
  - Mixed rewrite/delete/opaque interval over several files.
- Success criteria:
  - Active view after compaction is byte-for-byte equivalent to the active view
    before compaction.
  - Lease view still sees the protected older content.
  - Delta checkpoint contains explicit boundary markers instead of flattened
    contents that would resurrect lower files.
  - A negative test proves a plain flattened view checkpoint is rejected for
    boundary-sensitive intervals.

### M4: Copy-Through Accounting

- Purpose: prove non-reclaiming active-depth compaction is explicit and
  measurable.
- Cases:
  - Fully protected leased suffix with active depth over the guard.
  - Protected suffix plus small unleased prefix where reclaim and copy-through
    are both possible.
  - Large-file protected suffix where copy-through would add substantial bytes.
- Success criteria:
  - Active depth becomes bounded only when copy-through is explicitly requested
    by a guard or test option.
  - Metrics separate `bytes_added`, `bytes_removed`, and `pinned_bytes`.
  - Fully protected stacks report zero reclaim even if copy-through creates a
    new active checkpoint.
  - The benchmark summary calls out the duration of the transient peak.

### M5: Command Lease Admission Smoke

- Purpose: prove the public/ephemeral command policy does not start new
  long-running commands on an already unbounded lowerdir chain.
- Cases:
  - Start a command after retained unsquashed bytes cross the byte guard.
  - Start a command after active depth crosses the depth guard.
  - Keep the command running while later publishes trigger active-head cleanup.
- Success criteria:
  - Command launch either starts from a bounded compact generation or reports a
    policy-visible blocked/pressure reason.
  - This admission path does not remount already-running commands; live remount
    belongs to M6.
  - Later active-head compaction does not require the running command's mounted
    generation to change.
  - If Docker/live workspace E2E is unavailable, a LayerStack-level simulation
    must still run and the missing live gate must be reported explicitly.

### M6: Live Lease Parent-Prefix Normalization

- Purpose: prove a running command lease at `l4` does not force the physical
  parent prefix `n1..n3` to stay unmerged forever.
- Cases:
  - Oldest-to-newest same-file rewrite chain `n1, n2, n3, l4, n5, n6`.
  - Acquire the live command lease at `l4`, then publish `n5,n6`.
  - Normalize the live lease parent prefix to `[l4, C(n3,n2,n1)]` while the
    lease remains active.
  - Reclaim the top unleased interval `n5,n6`.
- Success criteria:
  - The lease stays active throughout the normalization and reclaim sequence.
  - The lease view at `l4` is unchanged after parent-prefix normalization.
  - The active view is unchanged after replacing `n3,n2,n1` with the compact
    parent.
  - Old physical parent layers `n3,n2,n1` are deleted after active rewrite and
    lease retarget.
  - Same-file retained payload becomes about `3S` while `l4` is still leased:
    `C(n6,n5) + l4 + C(n3,n2,n1)`.
  - Releasing `l4` followed by final squash reduces retained same-file payload
    to about `1S`.
  - Daemon integration must verify the real mount was remounted before
    retargeting the lease and deleting old lowerdir paths.

## 15. Required Tests

1. `fully_leased_stack_does_not_checkpoint`:
   - 50 layers, one lease over all 50.
   - Squash under pressure.
   - Assert no checkpoint is created and storage does not increase.

2. `unleased_prefix_compacts_above_leased_suffix`:
   - Lease 50-layer suffix.
   - Publish 10 newer unleased layers.
   - Squash.
   - Assert 10 layers become one checkpoint and old 10 are deleted.
   - Assert leased suffix remains.

3. `delete_above_leased_base_stays_deleted`:
   - Leased base writes `a.txt`.
   - Unleased layer deletes `a.txt`.
   - Compact unleased interval.
   - Assert active read still reports `a.txt` absent.

4. `opaque_above_leased_dir_preserves_boundary`:
   - Leased base has directory entries.
   - Unleased interval marks directory opaque.
   - Compact interval.
   - Assert lower entries stay hidden.

5. `copy_through_reports_non_reclaiming_bytes`:
   - Force hard active depth guard with protected suffix.
   - Build copy-through checkpoint.
   - Assert old protected layers remain and metrics report pinned bytes.

6. `command_launch_normalizes_unbounded_base`:
   - Build a stack above the byte or depth guard.
   - Acquire the command lease through the admission path.
   - Assert the mounted generation is bounded before command execution starts.
   - Assert later active-head squash does not retarget the running command.

7. `same_file_gap_compacts_around_single_protected_layer_then_reclaims`:
   - Build oldest-to-newest layers `n1, n2, n3, l4, n5, n6`.
   - Each layer rewrites the same `S`-byte file.
   - Protect only `l4`.
   - Squash under gap-compaction.
   - Assert retained payload is about `3S` while `l4` is protected.
   - Release `l4`, run cleanup/squash, and assert retained payload is about
     `1S`.

8. `mounted_l4_lease_keeps_lower_prefix_until_normalized_or_released`:
   - Build the same oldest-to-newest layers `n1, n2, n3, l4, n5, n6`.
   - Acquire a mounted lease at version `l4` using the current lowerdir model.
   - Squash under gap-compaction.
   - Assert the planner treats `n1..n3,l4` as protected and compacts only
     `n5,n6`, unless the lease was first normalized to a compact protected base.

9. `lease_parent_prefix_normalization_reclaims_prefix_while_lease_is_live`:
   - Build `n1, n2, n3, l4`, acquire a command lease, then publish `n5,n6`.
   - Normalize the live lease parent prefix to `[l4, C(n3,n2,n1)]`.
   - Keep the lease active while reclaiming `n5,n6`.
   - Assert the lease view still reads the `l4` snapshot.
   - Assert retained same-file payload is about `3S` while the lease is still
     active, then about `1S` after release and final squash.

## 16. Open Questions

1. Should delta checkpoints become the default for all interval compaction, even
   when a view checkpoint would be safe?
2. Should copy-through be allowed automatically for command launch admission, or
   should command launch wait for a reclaiming checkpoint only?
3. Should protected-byte accounting use logical file size or allocated blocks?
4. Can compact checkpoints be deduplicated by root hash to reduce copy-through
   cost?
