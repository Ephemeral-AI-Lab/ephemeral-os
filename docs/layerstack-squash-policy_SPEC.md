# LayerStack Squash Policy

Status: Draft
Date: 2026-06-16
Scope: `crates/daemon/layerstack`, command finalization, and LayerStack
storage-pressure reporting.

Related:
- `docs/command-ignored-state-publish_SPEC.md` owns command publish lane policy.
- `docs/command-git-occ-policy_SPEC.md` owns command `.git/**` behavior.

## 1. Intent

LayerStack must keep active stack depth bounded, keep unleased rewritten bytes
bounded, and make leased historical storage explicit when it cannot be reclaimed.

The current policy is primarily depth-triggered. `AUTO_SQUASH_MAX_DEPTH` is
`100`, so 50 accepted edits to a large file can remain unsquashed even when
disk pressure is already high. Depth is useful for read and manifest complexity,
but it is the wrong primary signal for large full-file rewrites.

This spec defines a byte-aware squash policy that treats depth, unleased payload
bytes, and large single-layer rewrites as independent pressure signals.

## 2. Goals

1. Bound active manifest depth under normal operation.
2. Bound unleased rewritten bytes under normal operation.
3. Preserve lease correctness: a leased snapshot must keep reading the manifest
   it leased until the lease is released.
4. Report lease-blocked history explicitly instead of silently growing storage.
5. Trigger squash for large rewrites even when stack depth is still low.
6. Run opportunistic reclaim after command finalization releases a command lease.
7. Keep auto-squash best-effort for v1 command publish success unless a future
   hard storage cap is configured.

## 3. Non-Goals

1. No deletion of layers that are still referenced by active leases.
2. No content-addressed global deduplication in this spec.
3. No byte-delta storage format for rewritten files in this spec.
4. No semantic merge of file contents during squash.
5. No promise that physical storage is O(1) while old leased snapshots remain
   live.

## 4. Definitions

| Term | Meaning |
| --- | --- |
| Active manifest depth | `active_manifest.layers.len()`. |
| Layer payload bytes | Total bytes of ordinary file and symlink payloads stored in one layer directory, excluding daemon metadata where practical. |
| Latest layer payload bytes | Payload bytes written by the layer produced by the just-accepted publish. |
| Checkpoint layer | A squash-produced layer that materializes the merged view of two or more lower layers. |
| Unsquashed active bytes | Payload bytes in active manifest layers that are still represented as individual publish layers rather than a checkpoint. |
| Eligible unsquashed bytes | Unsquashed active bytes in segments that can be squashed without crossing a leased head layer. |
| Lease-blocked bytes | Bytes retained because at least one active lease still references a layer that the active head no longer needs or cannot compact across. |
| Storage pressure | A measured condition where depth, unsquashed bytes, latest-layer bytes, or lease-blocked bytes exceeds configured policy thresholds. |

Layer payload byte accounting does not need to be perfect in v1. It must be
stable, monotonic enough for threshold decisions, and cheap enough to run after
publish and lease release. Filesystem block allocation precision is not
required for v1.

## 5. Current Baseline

Current behavior:

1. `CommitOptions` carries only `auto_squash_max_depth`.
2. The default `AUTO_SQUASH_MAX_DEPTH` is `100`.
3. Auto-squash runs after a successful publish.
4. Squash planning segments the active manifest around lease head layers.
5. Squash materializes checkpoint layers by projecting the merged segment.
6. Lease release removes layers from the released manifest only when those
   layers are no longer referenced by the active manifest or other leases.

Implication:

```text
50 small layers -> depth pressure is not triggered by default.
50 large full-file rewrites -> byte pressure can be large but is not a trigger.
```

## 6. Policy Invariants

1. Active stack depth is bounded by policy unless leases prevent a safe squash.
2. Unleased unsquashed active bytes are bounded by policy unless squash fails.
3. Leased history is either bounded by an explicit hard policy or reported as
   lease-blocked storage pressure.
4. A lease-blocked squash must not delete or rewrite the leased snapshot's
   required layers.
5. Byte-triggered squash uses the same correctness rules as depth-triggered
   squash.
6. Command publish success does not depend on best-effort squash success in v1,
   but the trace must report skipped or failed storage-pressure handling.
7. Reclaim after lease release is part of the storage policy, not optional
   background hygiene.

## 7. Configuration

Introduce a typed squash policy:

```rust
pub struct SquashPolicy {
    pub max_depth: usize,
    pub max_unsquashed_bytes: u64,
    pub large_rewrite_bytes: u64,
    pub lease_blocked_warn_bytes: u64,
    pub min_reduction: usize,
}
```

Recommended v1 defaults:

| Field | Default | Rationale |
| --- | ---: | --- |
| `max_depth` | `32` | Lowers active read/manifest depth without squashing after every publish. |
| `max_unsquashed_bytes` | `512 MiB` | Prevents moderate-depth stacks from retaining too many full-file rewrites. |
| `large_rewrite_bytes` | `128 MiB` | Forces immediate compaction attempts for one large rewritten file. |
| `lease_blocked_warn_bytes` | `512 MiB` | Reports retained history that cannot be reclaimed because leases are live. |
| `min_reduction` | `2` | Avoids checkpoint churn when compaction would not reduce depth or bytes meaningfully. |

Tests must be able to override every threshold with small values.

The final default values can be tuned from live workload measurements, but the
policy shape must not remain depth-only.

## 8. Trigger Rules

After a publish succeeds, collect a `LayerStackPressureSnapshot` and evaluate
these triggers:

| Trigger | Condition | Action |
| --- | --- | --- |
| Depth pressure | `active_depth > max_depth` | Run auto-squash. |
| Byte pressure | `eligible_unsquashed_bytes > max_unsquashed_bytes` | Run auto-squash. |
| Large rewrite | `latest_layer_payload_bytes > large_rewrite_bytes` | Run auto-squash immediately, even if depth is low. |
| Lease pressure | `lease_blocked_bytes > lease_blocked_warn_bytes` | Report `lease_blocked` pressure; do not delete leased layers. |

If multiple triggers apply, report all triggers and run at most one squash pass
for the publish.

Trigger evaluation order:

1. Measure latest layer payload bytes.
2. Measure active depth.
3. Segment the active manifest around leased heads.
4. Measure eligible unsquashed bytes for squashable segments.
5. Measure lease-blocked bytes.
6. Run squash if any compaction trigger is active.
7. Report lease pressure whether or not compaction was possible.

## 9. Squash Semantics

Squash remains a correctness-preserving checkpoint operation:

1. Select squashable segments from the active manifest.
2. Do not cross leased head layers.
3. Build checkpoint layers by projecting the merged segment.
4. Rewrite the active manifest to replace each segment with its checkpoint.
5. Remove unreferenced old layers only when no active manifest or lease still
   references them.

Byte-aware triggers do not change read semantics. They only change when squash
is attempted.

During a squash, peak storage can temporarily increase because checkpoint
payloads are written before old layers are removed. The implementation should
report `bytes_before`, `checkpoint_bytes`, and `bytes_after_reclaim` where
available.

## 10. Lease Policy

Leases are a hard correctness boundary.

When storage pressure is lease-blocked:

1. Keep all leased layers needed by live leases.
2. Emit a stable pressure report with:
   - active lease count,
   - leased layer count,
   - lease-blocked bytes,
   - oldest lease age when available,
   - trigger reasons that could not be satisfied,
   - largest blocked layer ids or paths, bounded to a small sample.
3. Do not fail command finalization solely because v1 best-effort squash was
   lease-blocked.

Future hard-cap behavior may reject new command preparations or snapshot leases
when lease-blocked bytes exceed a configured maximum. That is out of scope for
the first byte-aware squash implementation.

Long-running leases are expected. The system must not assume command or
workspace leases are short-lived. A lease that stays live for hours can be a
valid interactive workspace session, so the storage policy must be able to
compact around live leases instead of waiting indefinitely for release.

### 10.1 Lease-Blocked Squash Optimization

The planner must distinguish depth reduction from storage reduction.

Current squash can compact the tail behind a leased head layer. That improves
active manifest depth, but it can make storage worse while the lease remains
live because the original tail layers must be retained and the new checkpoint is
added beside them.

Example observed by benchmark:

```text
50 x 1 MiB same-file rewrites
lease held on the active 50-layer manifest

current partial squash while lease is held:
  before squash:        50 MiB, depth 50
  after squash:         51 MiB, depth 2
  after lease release:   2 MiB, depth 2
  later squash needed:   1 MiB, depth 1

defer storage-negative squash until lease release:
  while lease held:     50 MiB, depth 50
  after release+squash:  1 MiB, depth 1
```

V1 optimization policy:

1. For byte-pressure squash, do not build a checkpoint for a segment when every
   source layer in that segment is still retained by live leases. Such a segment
   has no immediate reclaimable source layers and any checkpoint increases live
   storage until the lease releases.
2. For depth-pressure squash, the planner may still compact a fully
   lease-retained segment, but it must report the estimated storage-negative
   tradeoff. This should be reserved for explicit depth pressure, not ordinary
   byte pressure.
3. The planner should prefer segments with positive immediate reclaim:

```text
reclaimable_source_bytes - estimated_checkpoint_bytes > 0
```

4. If exact checkpoint size is expensive to know before building it, use a
   conservative estimate and report the estimate source.
5. When a squash is skipped because all useful segments are lease-retained,
   report `lease_blocked` and mark the stack for post-release compaction.

This rule prevents storage-negative checkpoints while a lease pins the old
history.

### 10.2 Long-Running Lease Compaction

When a lease is expected to remain live, storage pressure must be reduced by
compacting the leased snapshot itself.

The desired end state is:

```text
active head representation: compact checkpoint for active visible state
long-running lease representation: compact checkpoint for leased visible state
old publish-layer chain: removed when neither representation references it
```

For example, with 50 same-file 1 MiB rewrites and one long-running lease on the
50-layer manifest, the goal is not to wait for release. The goal is to replace
the lease's 50-layer physical representation with a one-layer checkpoint for the
same logical snapshot, then allow active-head compaction to remove the old chain.

Long-running logical leases that are not mounted to a session are out of scope.
A long-running lease is assumed to back a live workspace session, which means
the session has already received concrete `layer_paths` and the mount namespace
may still hold the old lowerdirs. Therefore live-lease compaction is a mounted
workspace remount problem.

#### 10.2.1 Mounted Workspace Lease Remount

Mounted isolated workspaces receive concrete lowerdir paths at enter time. Their
mount namespace may keep using those paths even if the in-process lease record
is changed. For these leases, compaction must include a workspace remount or it
cannot safely reclaim the old lowerdirs.

Flow:

1. Mark the handle `compaction_pending`.
2. Wait for a safe boundary:
   - no in-flight file operation for the handle,
   - no command currently finalizing against the handle,
   - workspace manager holds the per-handle operation lock,
   - the runner can enter the holder mount namespace.
3. Build a checkpoint for the handle's leased logical manifest.
4. Ask the namespace runner to remount the overlay using:
   - the same `upperdir`,
   - the same `workdir`,
   - the compact checkpoint layer as the lower stack.
5. Update the persisted handle layer paths.
6. Atomically retarget the lease record and release old layer refs.
7. Resume new operations for the handle.

If the remount helper must use lazy detach because processes hold old mount
references, old lowerdirs may remain kernel-pinned until those references are
closed. In that case the system must report `mount_reference_blocked` rather
than claiming the bytes were reclaimed.

This makes long-running interactive workspaces supportable without assuming
short lease lifetimes. It also makes the correctness boundary explicit: the old
lowerdirs cannot be deleted until the running mount has moved to the compact
lower stack or all old mount references are gone.

#### 10.2.2 Lower Bound

Live-lease compaction cannot make storage smaller than the unique visible
content required by live snapshots. With one active head and one long-running
leased snapshot:

```text
minimum storage ~= unique content(active head) + unique content(leased snapshot)
```

The policy objective is to remove the extra factor from retained publish-layer
history:

```text
bad:    O(number_of_layers * rewritten_file_size)
target: O(unique_visible_versions_required_by_live_snapshots)
```

Achieving the target most efficiently may require future content-addressed file
objects, hardlink/reflink materialization, or per-file object dedup. This spec's
first implementation can still use checkpoint directories, but it must keep the
door open for object-level sharing between active and leased checkpoints.

## 11. Command Finalization Cleanup

Command finalization should run storage cleanup after releasing the command's
lease.

Required flow:

1. Capture and publish command changes according to command publish policy.
2. Release the command's LayerStack lease.
3. Remove layers that became unreferenced by that release.
4. Re-evaluate storage pressure after release.
5. If release makes a previously blocked segment squashable, run one
   opportunistic squash pass.
6. Report cleanup and squash outcome in command trace metadata.

This is the point where old versions can actually become reclaimable after a
command finishes.

Post-release compaction should be allowed to use a stronger target than normal
publish-time auto-squash. If release unblocks previously deferred byte pressure,
the post-release pass may compact toward depth `1` when doing so has positive
estimated byte reduction. This avoids leaving stacks such as
`[latest layer, checkpoint tail]` at double storage after the blocking lease has
gone away.

Post-release compaction is not sufficient for long-running leases. It remains a
cleanup path for normal command leases, but mounted workspace leases also need
live-lease compaction and remount as described above.

## 12. Trace And Reporting

Auto-squash trace events must distinguish policy triggers from skip reasons.

Required event names or equivalent structured fields:

| Event | Required details |
| --- | --- |
| `auto_squash_pressure_detected` | active depth, thresholds, trigger list, latest layer bytes, eligible unsquashed bytes, lease-blocked bytes. |
| `auto_squash_started` | trigger list, depth before, eligible bytes before, max depth, max unsquashed bytes. |
| `auto_squash_finished` | success, depth after, bytes after where available, checkpoint count, duration. |
| `auto_squash_skipped` | stable reason, trigger list, threshold values, lease/blocking metrics when relevant. |
| `layerstack_storage_pressure` | pressure class, lease count, leased bytes, oldest lease age when available. |
| `layerstack_reclaim_finished` | released lease id, removed layer count, removed bytes estimate, duration. |
| `post_release_squash_finished` | trigger list, depth before/after, bytes before/after, duration. |
| `lease_compaction_started` | lease id, owner, lease kind, depth before, bytes before, compaction mode. |
| `lease_compaction_finished` | lease id, depth after, bytes after, removed layer count, duration. |
| `lease_compaction_skipped` | lease id, stable reason, lease age, blocked bytes. |
| `workspace_remount_finished` | workspace handle id, old lower count, new lower count, remount duration, lazy detach used. |

Stable skip reasons:

| Reason | Meaning |
| --- | --- |
| `below_thresholds` | No configured depth or byte trigger was exceeded. |
| `too_shallow` | Existing depth planner found no useful segment. |
| `lease_blocked` | Live lease heads prevented safe compaction. |
| `min_reduction_unmet` | Squash would not reduce depth or bytes enough. |
| `max_depth_still_exceeded` | The safe plan would still violate max depth because of lease boundaries. |
| `storage_negative_while_lease_blocked` | The only available checkpoint would duplicate bytes retained by live leases. |
| `mount_reference_blocked` | A mounted workspace still holds old lowerdir references after remount or detach. |
| `unsafe_remount_boundary` | The workspace handle could not be quiesced for lowerdir remount. |
| `plan_failed` | Squash planning failed. |
| `squash_failed` | Checkpoint build or manifest rewrite failed. |

## 13. Implementation Plan

### Phase 1 - Metrics And Typed Policy

Tasks:

- Replace `CommitOptions { auto_squash_max_depth }` with a typed
  `SquashPolicy`, preserving a compatibility constructor for callers.
- Add payload-byte measurement for layer directories.
- Add `LayerStackPressureSnapshot`.
- Add unit tests for layer byte accounting.

Acceptance:

- Existing depth-only behavior can be represented as a policy.
- Tests can force byte thresholds with tiny values.

### Phase 2 - Byte-Aware Auto-Squash Trigger

Tasks:

- Evaluate depth, unsquashed bytes, and latest-layer bytes after publish.
- Trigger one auto-squash pass if any compaction trigger fires.
- Include trigger details in trace events.
- Lower the default `max_depth` from `100` to `32` unless measurement shows
  unacceptable publish latency.

Acceptance:

- A single layer larger than `large_rewrite_bytes` triggers auto-squash.
- Many small layers still trigger on depth.
- Many large layers trigger on bytes before reaching max depth.

### Phase 3 - Lease-Blocked Pressure Reporting

Tasks:

- Measure lease-blocked retained bytes.
- Teach squash planning to identify fully lease-retained segments.
- Skip storage-negative byte-pressure checkpoints while leases pin the source
  layers.
- Emit storage-pressure trace events when lease pressure exceeds the warning
  threshold.
- Keep publish success independent from best-effort pressure reporting.

Acceptance:

- With a live old lease, squash does not delete required layers.
- Trace reports `lease_blocked` with count and byte metrics.
- Byte-pressure squash does not add a checkpoint when every useful segment is
  fully retained by active leases.

### Phase 4 - Command Finalization Reclaim

Tasks:

- After command finalization releases its lease, run reclaim.
- Re-evaluate pressure after release.
- Run one opportunistic squash pass if release made compaction possible,
  including a depth-1 pass when it has positive byte reduction.
- Add command trace metadata for reclaim and post-release squash outcome.

Acceptance:

- Releasing the last lease removes old unreferenced layers.
- A byte-pressure stack that was lease-blocked can compact after release from
  `50 x 1 MiB` retained rewrites to one visible 1 MiB checkpoint.

### Phase 5 - Long-Running Lease Compaction

Tasks:

- Add lease metadata: owner, kind, created time, and whether layer paths have
  been mounted into a live workspace session.
- Add a lease compaction planner that can build a checkpoint for a leased
  logical manifest.
- Support mounted workspace remount at a safe handle boundary.
- Persist compacted handle layer paths after remount.
- Report remount-blocked old lowerdir references.

Acceptance:

- A mounted workspace can be compacted by remounting the overlay to a compact
  lower stack while preserving its upperdir and workdir.
- If the mount namespace still pins old lowerdirs after remount, storage
  pressure reports `mount_reference_blocked` instead of claiming reclaim.
- Active-head squash can reclaim old publish layers once all live leases are
  represented by compact checkpoints or remounted compact lower stacks.

## 14. Verification

Focused unit tests:

```sh
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack squash
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack auto_squash
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation command::
```

Package gates:

```sh
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation --all-targets
cargo run -p xtask -- package
git diff --check
```

Live E2E should be added when command finalization lease-release behavior or
trace response shape changes.

## 15. Required Test Scenarios

1. `max_depth` trigger:
   - Configure `max_depth = 3`.
   - Publish four small layers.
   - Assert auto-squash runs and active depth drops unless lease-blocked.

2. `max_unsquashed_bytes` trigger:
   - Configure `max_unsquashed_bytes = 8 KiB`.
   - Publish several 4 KiB rewrites before max depth.
   - Assert auto-squash runs because byte pressure exceeded the threshold.

3. `large_rewrite_bytes` trigger:
   - Configure `large_rewrite_bytes = 4 KiB`.
   - Publish one 8 KiB file rewrite.
   - Assert auto-squash is attempted even when depth is below max depth.

4. Lease-blocked pressure:
   - Acquire a lease on an old manifest.
   - Publish enough bytes to exceed pressure thresholds.
   - Assert old leased content remains readable.
   - Assert trace reports `lease_blocked` with byte metrics.

5. Post-release reclaim:
   - Release the blocking lease.
   - Assert unreferenced layers are removed.
   - Assert opportunistic squash or cleanup reduces active depth or retained
     bytes.

6. Storage-negative lease-blocked squash:
   - Publish 50 same-path 1 MiB rewrites.
   - Acquire a lease on the active manifest.
   - Trigger byte-pressure squash.
   - Assert no checkpoint is added while the lease pins all source layers.
   - Release the lease and run post-release compaction.
   - Assert final payload is 1 MiB and depth is 1.

7. Long-running mounted workspace compaction:
   - Enter an isolated workspace from a 50-layer manifest.
   - Build a compact checkpoint for the leased manifest.
   - Remount the workspace overlay with the compact lower stack.
   - Assert reads still return the leased snapshot content.
   - Assert persisted handle layer paths point to the compact lower stack.

8. Publish success independence:
   - Force auto-squash failure after a successful publish.
   - Assert the publish result remains successful in v1.
   - Assert trace reports `squash_failed`.

## 16. Open Questions

1. Should `max_unsquashed_bytes` default be absolute only, or
   `min(512 MiB, workspace_size / 4)`?
2. Should large rewrites trigger synchronous squash in command finalization, or
   enqueue a background compaction pass when available?
3. Should future hard caps reject new leases, new commands, or only command
   finalization publishes?
4. Should checkpoint layers be re-squashed when their payload grows beyond a
   threshold, or are checkpoint layers considered the compacted representation?
5. Should payload-byte accounting use logical file size or allocated disk blocks
   where supported?
