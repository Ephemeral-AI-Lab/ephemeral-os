# LayerStack Squash Policy

Status: Draft
Date: 2026-06-16
Scope: `crates/daemon/layerstack`, command finalization, and LayerStack
storage-pressure reporting.

Related:
- `docs/command-ignored-state-publish_SPEC.md` owns command publish lane policy.
- `docs/command-git-occ-policy_SPEC.md` owns command `.git/**` behavior.
- `docs/layerstack-leased-squash-gap-compaction_SPEC.md` owns the focused
  design for compacting unleased intervals around leased layers without adding
  non-reclaiming storage.

## 1. Intent

LayerStack must keep active stack depth bounded, keep unleased rewritten bytes
bounded, and make leased historical storage explicit when it cannot be reclaimed.

The current policy is primarily depth-triggered. `AUTO_SQUASH_MAX_DEPTH` is
`100`, so 50 accepted edits to a large file can remain unsquashed even when
disk pressure is already high. Depth is useful for read and manifest complexity,
but it is the wrong primary signal for large full-file rewrites.

This spec defines a byte-aware squash policy that treats depth, unleased payload
bytes, and large single-layer rewrites as independent pressure signals.
The detailed algorithm for leased-layer gap compaction is split into the
focused companion spec listed above.

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
8. A long-running lease that owns a mounted isolated workspace may be compacted
   by remounting that live workspace onto a compact checkpoint while the
   workspace is idle.
9. A public/ephemeral command lease is process-owned while it is open. The
   system must not switch lowerdirs under the running command. Its policy is
   launch-time lowerdir normalization, running-command pressure reporting, and
   finalization-time reclaim.

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

## 10. Lease-Aware Unleased Interval Compaction

The compactor should distinguish leased layers from unleased layers at the
layer-ref level, not only at lease heads.

Definitions:

| Term | Definition |
| --- | --- |
| Protected layer | A layer ref with live lease refcount greater than zero. |
| Unleased interval | A maximal contiguous run in the active manifest where every layer ref has zero live lease refcount. |
| Fence layer | A protected layer kept unchanged in the active manifest or kept alive only by a lease. |
| Reclaiming checkpoint | A checkpoint replacing only unleased layers, so old input layers can be deleted after commit. |
| Copy-through checkpoint | A checkpoint that reads protected layers to bound active depth but cannot delete those protected inputs. |

The default squash planner should compute:

```text
protected = union(all layer refs in live lease manifests)
active = current active manifest, newest first
intervals = maximal active runs where layer not in protected
```

Then:

1. Build checkpoints only for unleased intervals whose depth or bytes justify
   compaction.
2. Keep protected layers as immutable fences.
3. After manifest rewrite, delete only old layers that are absent from the new
   active manifest and have zero lease refcount.
4. Never checkpoint a protected interval solely because it appears below a lease
   head. That creates extra bytes without reclaiming anything.

This gives safe partial reclaim while leases are live. Example:

```text
active before: [N3, N2, N1, P50, P49, ..., P1]
leased:        [P50, P49, ..., P1]

active after:  [Bnew, P50, P49, ..., P1]
deleted:       N3, N2, N1
kept:          leased P* layers
```

The active chain is still bounded by the protected suffix, but new unleased
growth is reclaimed instead of accumulating forever.

### Boundary-Preserving Checkpoints

Unleased interval compaction cannot always use a flattened projection
checkpoint. If the interval sits above protected fence layers, deletes and
opaque-directory markers inside the interval may be needed to hide files in the
protected layers below.

Unsafe example:

```text
active before: [delete a.txt, Pbase(a.txt)]
leased:        [Pbase(a.txt)]
```

If the unleased interval `[delete a.txt]` is flattened by projecting it into an
empty directory, the checkpoint becomes empty and `a.txt` incorrectly reappears
from `Pbase`.

Therefore partial compaction must use one of two checkpoint modes:

| Mode | Safe when | Behavior |
| --- | --- | --- |
| View checkpoint | The compacted interval is bottom-closed, meaning there are no kept lower layers whose contents can be affected by interval deletes/opaque markers. | Project the merged view to a normal checkpoint directory. |
| Delta checkpoint | The compacted interval has kept/protected lower layers below it. | Preserve boundary effects by carrying writes, symlinks, whiteouts, and opaque markers from the interval into the checkpoint. |

The delta checkpoint is the safer default for lease-aware interval compaction.
It should aggregate the interval as layer changes, not as a pure filesystem
view:

1. Read changes from newest to oldest across the interval.
2. For each path, keep the newest effective operation.
3. Materialize writes and symlinks normally.
4. Materialize deletes as whiteout markers, even if the deleted path does not
   exist inside the interval itself.
5. Materialize opaque directories as opaque markers and include visible entries
   above the opaque marker inside the interval.
6. Validate that replacing the interval with the delta checkpoint preserves
   reads against a sampled or full manifest when test scale allows it.

If the implementation cannot build a boundary-preserving checkpoint for a
candidate interval, it must skip that interval and report
`boundary_preservation_required`.

### Copy-Through For Active Depth

Unleased interval compaction reclaims bytes, but it may not fully bound active
depth when a live lease protects a long suffix. Copy-through is a separate
operation:

```text
active before: [Bnew, P50, P49, ..., P1]
leased:        [P50, P49, ..., P1]

active after:  [Bactive]
leased kept:   [P50, P49, ..., P1]
deleted now:   Bnew if unleased
not deleted:   P* until lease exit
```

Copy-through can keep future active mounts bounded, but it does not reclaim the
protected leased inputs. It should only run when:

1. active mount depth would otherwise exceed a hard guard,
2. future command launch would otherwise be blocked,
3. or the copied-through checkpoint can be shared/deduplicated with an existing
   compact base.

It must report `copy_through_checkpoint_created` with added bytes and protected
bytes still pinned.

## 11. Lease Policy

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
valid interactive workspace session, so the storage policy must compact around
live leases instead of waiting indefinitely for release.

### Lease-Blocked Squash Optimization

The planner must distinguish depth reduction from storage reduction. If every
source layer in a candidate segment is still retained by a live lease, building
a new checkpoint can make storage worse until the lease releases.

Observed benchmark shape:

```text
50 x 1 MiB same-file rewrites
lease held on the active 50-layer manifest

partial squash while lease is held:
  before squash:        50 MiB, depth 50
  after squash:         51 MiB, depth 2
  after lease release:   2 MiB, depth 2
  later squash needed:   1 MiB, depth 1
```

For byte-pressure squash, do not build a checkpoint for a segment when every
source layer in that segment is still retained by live leases. Such a segment
has no immediate reclaimable source layers and any checkpoint increases live
storage until release. For hard depth pressure, the planner may still build a
non-reclaiming checkpoint, but it must report the added bytes and the protected
bytes still pinned.

## 12. Command Finalization Cleanup

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

Post-release compaction may use a stronger target than normal publish-time
auto-squash. If release unblocks previously deferred byte pressure, the
post-release pass may compact toward depth `1` when doing so has positive
estimated byte reduction. This avoids leaving stacks such as
`[latest layer, checkpoint tail]` at double storage after the blocking lease has
gone away.

## 13. Public/Ephemeral Command Lease Policy

An ephemeral or public command lease exists because a command process is
running. That process may hold open file descriptors, current working
directories, memory mappings, or overlay copy-up state inside its workspace.
The system must treat that as an unsafe in-place remount boundary.

The command policy is therefore an RCU-style mount-generation policy with a
hard launch invariant:

```text
No public/ephemeral command process may start on an unbounded lowerdir chain.
```

1. Before launching the command process, acquire its snapshot lease.
2. Evaluate the snapshot's lowerdir depth, retained unsquashed bytes, and
   projected mount cost.
3. If the snapshot exceeds command mount thresholds, or if the system cannot
   prove the snapshot is already a bounded mount generation, build a compact
   checkpoint for that snapshot before the process starts.
4. Mount the command workspace from the compact checkpoint and retarget the
   command lease to that checkpoint before `exec`.
5. Once the process is running, never remount or switch that command's lowerdir
   in place.
6. If other publishes make that running command a historical lease, report
   active-command storage pressure instead of reclaiming its base.
7. When the command exits, capture/publish according to command policy, release
   the lease, and immediately run reclaim plus one opportunistic squash pass
   when pressure remains.

The system must distinguish two physical references for command storage:

| Reference | Owner | Can retarget while process runs? | Purpose |
| --- | --- | --- | --- |
| Command mount generation | running process | No | Keeps the actual lowerdirs used by the process valid until exit. |
| Logical snapshot metadata | command finalization | Only if equivalent and old mount generation remains protected | Lets finalization route capture against an equivalent compact snapshot. |

This changes the long-running command storage shape from:

```text
running command pins original lowerdir chain
```

to:

```text
running command pins compact launch checkpoint + command upper/scratch state
```

for commands launched after the normalization policy is active. It does not
solve commands that were already launched with a long lowerdir chain; those can
only be reported as `active_command_lease_blocked` until they exit unless a
future process-freeze/remount protocol is added. If such a legacy unnormalized
command may run indefinitely, the system cannot guarantee bounded storage
without an operator policy that cancels, restarts, or externally migrates that
process.

Recommended command-specific thresholds:

| Field | Default | Rationale |
| --- | ---: | --- |
| `command_mount_max_depth` | `32` | Avoids launching new commands on long lowerdir chains. |
| `command_mount_max_unsquashed_bytes` | `512 MiB` | Avoids starting long commands that pin large rewrite history. |
| `command_mount_large_rewrite_bytes` | `128 MiB` | Normalizes snapshots after a large rewritten file before long command lifetime starts. |
| `active_command_blocked_warn_bytes` | `512 MiB` | Reports running commands that are preventing reclaim. |
| `active_command_blocked_hard_bytes` | unset in v1 | Future cap for rejecting new publishes or new commands when active commands pin too much history. |

Launch-time compaction is allowed to add startup latency because it happens
before any process state exists. For interactive latency, the system may skip
compaction only when the snapshot is already below the bounded-generation
thresholds. Once thresholds are crossed, launch-time normalization is a hard
admission gate: if compaction fails, reject the command start and report
`command_mount_compaction_failed`. Starting a potentially long-running command
on an unbounded lowerdir chain recreates the stuck-process pollution problem.

### Squash While Command Leases Are Active

When a public/ephemeral command lease is active during a squash, the optimizer
must keep the active head bounded without deleting lowerdirs still used by the
running process.

Default mode is `bounded_generation_copy_through`:

1. Classify live leases by lifecycle:
   - `process_owned_command`: public/ephemeral command process is running.
   - `idle_isolated`: isolated workspace is open and has no active command.
   - `metadata_only`: no mounted process or workspace depends on the lease.
2. Split candidate squash segments into:
   - `reclaimable`: old layers can be removed immediately after manifest rewrite.
   - `bounded_command_generation`: a running command pins a compact checkpoint
     that is safe to retain as one bounded historical generation.
   - `unnormalized_command_blocked`: a running command mount generation still
     physically uses the old layers.
3. Build reclaiming checkpoints for `reclaimable` segments.
4. For `bounded_command_generation` segments, the active head may copy through
   the command's compact base into a new active checkpoint. The old compact base
   remains pinned by the command, but the active manifest no longer carries that
   historical generation forward.
5. Do not leave a stuck command's compact base inside the active chain merely
   because the command is still running. That is the pollution case this policy
   prevents.
6. For `unnormalized_command_blocked` segments, report
   `active_command_lease_blocked` and `blocked_prefix_deferred` with command id,
   lease age, lowerdir count, blocked bytes, and oldest blocked layer. The
   system may build a non-reclaiming active checkpoint to keep future active
   mounts usable, but it must report `legacy_unnormalized_command_pinned` and
   account for the fact that old bytes cannot be reclaimed until process exit.
7. After the command exits, finalization releases the mount generation and the
   blocked generation becomes eligible for reclaim.

This makes the bounded steady state:

```text
active head checkpoint + one compact base per live command generation + command upper/scratch
```

not:

```text
active head depends on every layer since the oldest stuck command started
```

The remaining bound is therefore the number and size of live command
generations. That must be controlled by active command concurrency limits,
per-command upper/scratch quotas, generation deduplication by root hash, and
operator-visible pressure for long-running commands.

A background shadow checkpoint for a running command is optional and should be
budgeted separately:

1. It may compact the command's pinned snapshot while the command runs.
2. It must not replace the command mount generation or allow old lowerdirs to be
   deleted before process exit.
3. It may be used at finalization if the compact root hash matches the original
   command snapshot.
4. It should be skipped under storage pressure unless it is needed to reduce
   finalization latency.

## 14. Mounted Isolated Lease Compaction

The lease policy above is not enough for a long-running isolated session. A
mounted isolated workspace pins its lowerdir list even if the active head can be
compacted. The system can reduce retained lowerdir storage while the session is
still open only by changing both physical owners:

1. Build a compact checkpoint for the mounted session's leased snapshot.
2. Remount the live isolated workspace with the same `upperdir` and `workdir`
   and the compact checkpoint as its lowerdir list.
3. Update the workspace handle's physical `layer_paths` so future isolated
   commands route through the compact lowerdir.
4. Retarget the active lease's refcounts from the old snapshot layers to the
   compact checkpoint while preserving the logical lease id.
5. Squash the active public head so active LayerStack state also stops
   referencing the old layer chain.
6. Reclaim old layers that are referenced by neither the active manifest nor
   any remaining lease.

This turns same-file repeated rewrite retention from `O(L * file_size)` toward
`O(C * file_size)` for open isolated sessions, where `C` is the bounded number
of compact checkpoints needed for the active head and mounted session snapshots.
The system should report `mount_reference_blocked` if a process keeps old mount
references, cwd handles, or open file descriptors that prevent the kernel from
dropping old lowerdir backing storage after remount.

This mechanism is not the ephemeral/public command solution. A long-running
ephemeral/public command may own a mounted command workspace, but that mount is
process-owned while the command runs. It must be normalized before process
launch or reclaimed after process exit, not remounted in place.

## 15. Trace And Reporting

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
| `lease_compaction_started` | caller id, lease id, before layer count, active depth, storage bytes before. |
| `lease_compaction_finished` | remounted layer count, lease retargeted, active depth after, layer dirs after, storage bytes after. |
| `workspace_remount_finished` | caller id, workspace handle id, old lowerdir count, new lowerdir count. |
| `command_mount_compaction_started` | command id, lease id, lowerdir count, retained bytes before, thresholds. |
| `command_mount_compaction_finished` | command id, compacted lowerdir count, retained bytes after, duration, launch delayed seconds. |
| `active_command_lease_blocked` | command id, lease age, blocked bytes, lowerdir count, process state summary. |
| `command_squash_bounded_generation` | command lease count, reclaimable bytes, copied-through bounded generations, unnormalized blocked bytes. |
| `non_reclaiming_checkpoint_created` | trigger, added bytes, active depth before/after, blocked command lease ids. |
| `command_shadow_compaction_finished` | command id, compacted snapshot bytes, usable_at_finalization, duration. |
| `lease_interval_compaction_started` | protected layer count, unleased interval count, reclaimable bytes, boundary-preserving mode. |
| `lease_interval_compaction_finished` | checkpoint count, removed layer count, removed bytes, skipped interval count. |
| `copy_through_checkpoint_created` | trigger, active depth before/after, added bytes, protected bytes still pinned. |

Stable skip reasons:

| Reason | Meaning |
| --- | --- |
| `below_thresholds` | No configured depth or byte trigger was exceeded. |
| `too_shallow` | Existing depth planner found no useful segment. |
| `lease_blocked` | Live lease heads prevented safe compaction. |
| `min_reduction_unmet` | Squash would not reduce depth or bytes enough. |
| `max_depth_still_exceeded` | The safe plan would still violate max depth because of lease boundaries. |
| `plan_failed` | Squash planning failed. |
| `squash_failed` | Checkpoint build or manifest rewrite failed. |
| `mount_reference_blocked` | Remount completed but old lowerdir storage still appears kernel-pinned. |
| `unsafe_remount_boundary` | A live command/process state made remount unsafe for this attempt. |
| `active_process_running` | Public/ephemeral command lease cannot be remounted because the process is still running. |
| `command_mount_compaction_failed` | Launch-time command snapshot compaction failed before process start. |
| `blocked_prefix_deferred` | A squash segment was skipped because active command mounts still pin its old layers. |
| `legacy_unnormalized_command_pinned` | A command that started before launch normalization still pins an unbounded lowerdir chain. |
| `boundary_preservation_required` | A partial unleased interval needs delta-checkpoint semantics that are not available for this attempt. |

## 16. Implementation Plan

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

### Phase 3 - Lease-Aware Unleased Interval Compaction

Tasks:

- Replace lease-head-only squash planning with a protected-layer set derived
  from live lease refcounts.
- Plan compaction over maximal unleased intervals.
- Add boundary-preserving delta checkpoint support for intervals above protected
  lower layers.
- Delete only old layers with zero lease refcount after manifest rewrite.
- Report skipped intervals when boundary preservation is required but not
  available.

Acceptance:

- With a lease over a 50-layer suffix and 10 newer unleased layers, compaction
  can collapse and reclaim the 10 newer layers without touching the leased
  suffix.
- A delete in an unleased interval above a leased lower file remains a whiteout
  after compaction; the lower file must not reappear.
- A fully leased active stack does not create non-reclaiming checkpoints merely
  to reduce depth.

### Phase 4 - Lease-Blocked Pressure Reporting

Tasks:

- Measure lease-blocked retained bytes.
- Emit storage-pressure trace events when lease pressure exceeds the warning
  threshold.
- Keep publish success independent from best-effort pressure reporting.

Acceptance:

- With a live old lease, squash does not delete required layers.
- Trace reports `lease_blocked` with count and byte metrics.

### Phase 5 - Command Finalization Reclaim

Tasks:

- After command finalization releases its lease, run reclaim.
- Re-evaluate pressure after release.
- Run one opportunistic squash pass if release made compaction possible.
- Add command trace metadata for reclaim and post-release squash outcome.

Acceptance:

- Releasing the last lease removes old unreferenced layers.
- A byte-pressure stack that was lease-blocked can compact after release.

### Phase 6 - Command Launch Snapshot Normalization

Tasks:

- Add command-preparation pressure evaluation before process spawn.
- Compact the command snapshot into a checkpoint when launch thresholds are
  exceeded.
- Retarget the command lease to the compact checkpoint before mounting and
  starting the command.
- Refuse in-place remount for running public/ephemeral commands and report
  `active_command_lease_blocked` when they pin blocked bytes.
- Teach auto-squash to use `bounded_generation_copy_through` mode when
  process-owned command leases are live: retain each running command's compact
  base, but let the active head copy through that bounded generation so the
  stuck command does not remain in the active manifest chain.
- Track command mount-generation refs separately from logical snapshot metadata
  if shadow compaction is introduced.
- Reject new command starts when launch-time normalization is required but
  cannot produce a bounded mount generation.
- Reuse finalization reclaim from Phase 4 after the command exits.

Acceptance:

- A newly launched long-running command can pin one compact checkpoint instead
  of a long original lowerdir chain.
- A running command is never remounted in place.
- Squash while a command is running keeps the active head bounded by copying
  through compact command generations while preserving the command's old mount
  generation.
- A running command that blocks reclaim reports command id, lease age, lowerdir
  count, and blocked bytes.
- A legacy running command that still pins an unbounded chain reports
  `legacy_unnormalized_command_pinned`.
- After the command exits, lease release makes old layers reclaimable and one
  opportunistic squash pass runs if pressure remains.

### Phase 7 - Live Isolated Remount Compaction

Tasks:

- Add an internal/test-proven path to compact an arbitrary leased snapshot into
  a single checkpoint lowerdir.
- Add a workspace-manager remount path that preserves `upperdir`/`workdir` and
  swaps only the lowerdir list.
- Retarget the active lease's refcounts to the compact checkpoint.
- Run active-head squash after lease retarget so old layers are not still owned
  by the public head.
- Refuse or defer remount while that caller has active commands until a
  process-level safety protocol exists.

Acceptance:

- An open isolated session still reads its leased public snapshot after remount.
- Private upperdir writes remain visible after remount.
- The session keeps one active lease.
- Layer dirs drop from linear retained rewrite count to a bounded constant.
- Calling the remount path for an ephemeral/public caller reports `not_open`.

## 17. Verification

The space/time benchmark harness and the current local results are documented
in `docs/layerstack-space-time-benchmark_RESULTS.md`. The key result is that
retained same-file rewrites grow as `O(L * file_size)`, same-snapshot lease
handles do not multiply lowerdir storage, and live isolated remount compaction
reduces same-file retained history from 50 MiB to 2 MiB while the lease remains
open. The current remount path can still hold two compact checkpoints for unique
file snapshots.

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

## 18. Required Test Scenarios

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

6. Publish success independence:
   - Force auto-squash failure after a successful publish.
   - Assert the publish result remains successful in v1.
   - Assert trace reports `squash_failed`.

7. Live isolated remount compaction:
   - Publish many same-file rewrites.
   - Enter isolated mode.
   - Write a private upperdir file.
   - Compact/remount the open isolated workspace.
   - Assert public and private reads remain correct and layer dirs are bounded.

8. Ephemeral/public caller distinction:
   - Call mounted-remount compaction for a caller with no isolated workspace.
   - Assert it returns `not_open`.
   - Cover long-running public commands separately through command scratch and
     finalization pressure policy.

9. Lease-aware unleased interval compaction:
   - Acquire a lease over a 50-layer suffix.
   - Publish 10 newer unleased layers.
   - Run compaction.
   - Assert the 10 newer layers collapse to one checkpoint and are deleted.
   - Assert the 50 leased layers are unchanged and still readable by the lease.

10. Boundary-preserving delta checkpoint:
    - Base leased layer writes `a.txt`.
    - New unleased layer deletes `a.txt`.
    - Compact the unleased interval above the leased base.
    - Assert active read of `a.txt` remains absent after compaction.
    - Assert the compact checkpoint contains a whiteout or equivalent boundary
      delete marker.

11. Copy-through checkpoint accounting:
    - Hold a live lease over a long protected suffix.
    - Force a hard active mount-depth guard.
    - Build an active copy-through checkpoint.
    - Assert protected old layers remain on disk and are reported as pinned.
    - Assert active depth is bounded and added checkpoint bytes are reported.

12. Command launch snapshot normalization:
   - Build a public/ephemeral command snapshot whose lowerdir chain exceeds
     command launch thresholds.
   - Start a long-running command.
   - Assert the command lease is retargeted to one compact checkpoint before
     process launch.
   - Assert no in-place remount attempt is made while the process is running.

13. Active command pressure reporting:
    - Start a long-running command on a compacted snapshot.
    - Publish enough later changes to make the command lease historical.
    - Assert pressure reports `active_command_lease_blocked` rather than
      deleting the command's base.
    - After the command exits, assert release plus cleanup reclaims
      unreferenced layers.

14. Bounded-generation squash with running command lease:
    - Start a long-running public/ephemeral command on a normalized compact
      snapshot.
    - Publish additional layers after the command starts.
    - Run auto-squash under pressure.
    - Assert the active manifest can become one compact checkpoint even though
      the command still pins its compact launch base.
    - Assert final storage is active checkpoint plus command compact base plus
      command upper/scratch, not the full post-launch chain.

15. Legacy unnormalized stuck command:
    - Simulate or start a command that pins a 50-layer lowerdir chain.
    - Publish additional layers.
    - Run auto-squash under pressure.
    - Assert the active head may be compacted for future mounts, but the old
      50-layer command generation remains unreclaimed.
    - Assert trace reports `legacy_unnormalized_command_pinned` and blocked
      bytes.

## 19. Open Questions

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
6. What process-level safety protocol is needed to remount while a command is
   actively running inside the isolated workspace instead of between commands?
7. Should active and isolated checkpoints be deduplicated when they represent
   the same logical root hash, reducing the constant from two checkpoints to one?
8. Should command launch snapshot normalization be synchronous when thresholds
   are crossed, or should it fail/queue when compaction would make startup
   latency exceed a configured budget?
9. Should background command shadow compaction exist at all, or is
   finalization-time compaction fast enough once launch-time normalization is in
   place?
10. Should unnormalized command starts be rejected whenever depth exceeds `1`,
    or only when depth/byte thresholds are crossed?
