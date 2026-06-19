# Live Run Remount During Squash Implementation Report

Date: 2026-06-17

## Executive Summary

This report documents the current implementation work for reclaiming LayerStack
storage while commands still hold open isolated-workspace leases. The current
codebase has three relevant mechanisms:

1. New command admission can normalize the active LayerStack before a command
   lease is acquired.
2. Lease-aware squash can compact unleased gaps around protected lease heads.
3. The experimental isolated-workspace remount path can quiesce a running
   command, mount a compact lowerdir set, verify the mount, retarget the lease,
   squash the active stack, and resume the command.

The live remount path is implemented and covered by E2E tests, but it is still
exposed as an isolated-workspace test operation. It is not yet wired into the
ordinary production auto-squash trigger that runs after every publish.

## Current Status

Implemented:

- `remountable` command opt-in plumbing.
- Process-group quiesce/resume for running isolated commands.
- `/proc` inspection for cwd, root, open file descriptors, mapped files, and
  mountinfo.
- Staged overlay remount with mountinfo lowerdir verification.
- Lease metadata retarget after verified mount switch.
- Block-and-report behavior when a live remount cannot be proven safe.
- LayerStack primitives for:
  - command snapshot normalization before launch,
  - lease-aware gap reclaim around protected layers,
  - leased-parent compaction for remount,
  - full snapshot compaction for remount.
- Live E2E coverage with many-file trees, large files, process fanout, multiple
  running commands, historical leases, and real concurrent pip install.

Not yet production complete:

- The live remount operation is not yet invoked automatically from the normal
  `LayerStack::squash()` or publish finalization path.
- The production policy for when to choose live remount versus pressure-only
  reporting still needs to be wired into command finalization or the daemon's
  storage-pressure maintenance loop.
- The current live remount experiment compacts the mounted snapshot to one
  lowerdir. The logical `[lease-head, compact-parent]` primitive also exists,
  but production still needs to choose which representation is preferable for
  each workspace type.

## Code Inventory

### Command Remount Safety Inspection

Main file:

- `crates/daemon/operation/src/command/service/remount.rs`

Key responsibilities:

- `CommandRemountInspection` records active command count, remountable command
  count, process count, quiesced process count, pinned cwd/root/fd/mapped-file
  counts, mountinfo check count, block reason, and resume state.
- `CommandOps::begin_live_remount_for_caller` enumerates active commands owned
  by a caller, requires isolated commands marked `remountable`, freezes their
  process groups, inspects pinned paths, and keeps the process groups stopped
  only if the session is safe to remount.
- `CommandRemountQuiesce::resume` and `Drop` resume held process groups so an
  early return does not leave commands stopped.

Safety checks:

- All active commands for the caller must be remountable.
- Every process group must be available.
- `SIGSTOP` must stop the whole process group within the timeout.
- Process membership must remain stable across freeze.
- No process may have `cwd` or `root` inside the workspace mount.
- No open fd may point inside the workspace mount.
- No mapped file may point inside the workspace mount.
- The process mountinfo must still contain the expected workspace mount.

Important code paths:

- `begin_live_remount_for_caller`: `crates/daemon/operation/src/command/service/remount.rs`
- `inspect_isolated_command_process_group_linux`: same file
- `inspect_pinned_paths`: same file
- `mountinfo_has_workspace_mount`: same file

### Workspace Runtime Remount Orchestration

Main file:

- `crates/daemon/core/src/runtime/workspace.rs`

Key flow:

```text
1. Resolve the layer stack root.
2. Mark the isolated-network workspace handle as remount_pending.
3. Begin command quiesce for the caller.
4. If all active commands are safe:
   a. compact the mounted snapshot,
   b. remount workspace_root with the compact lowerdir list,
   c. verify mount state,
   d. retarget the LayerStack lease,
   e. run active-stack squash cleanup,
   f. resume the process group.
5. If any safety check is uncertain:
   a. resume the process group,
   b. emit pressure-only blocked report,
   c. do not retarget,
   d. do not delete mounted lowerdirs.
6. Clear remount_pending.
```

Important code paths:

- `compact_remount_open_workspace_for_test_resolved_locked`
- `compact_remount_open_workspace_marked_pending`
- `WorkspaceState::compact_remount_open_workspace_for_test`
- `WorkspaceState::blocked_remount_report_for_test`

The successful path verifies:

- `remounted.handle.layer_paths == compaction.layer_paths`
- `remounted.remount.mount_verified == true`
- `retarget_lease_manifest(...) == true`
- process group is resumed after retarget and cleanup

The blocked path reports:

- `fallback_compaction_enabled: false`
- `fallback_compacted_layers: 0`
- `fallback_removed_layers: 0`
- `fallback_bytes_added: 0`
- pinned bytes and parent-prefix bytes
- before/after manifest depth, layer dirs, and storage bytes unchanged

### Isolated Workspace Manager Remount

Main files:

- `crates/daemon/workspace/src/isolated/manager/lifecycle.rs`
- `crates/daemon/workspace/src/isolated/remount.rs`

`IsolatedManager::remount_with_layers` validates the caller and new layer list,
calls the runtime overlay remount, rejects the operation unless the runtime
reports `mount_verified`, then updates and persists the handle's `layer_paths`.

`RemountOverlayReport` carries:

- mount verification result,
- staged switch result,
- staging verification,
- rollback cleanup result,
- mount namespace,
- mountinfo fs type,
- actual and expected lowerdir counts,
- exact lowerdir verification,
- optional probe read result.

### Linux Namespace Overlay Switch

Main file:

- `crates/daemon/namespace-process/src/runner/setns.rs`

The live remount implementation mounts the new overlay at a staging mountpoint,
verifies it, moves the old workspace mount to a rollback mountpoint, moves the
new mount onto `workspace_root`, restores the mount mask, verifies the final
workspace mount, and unmounts the old rollback mount.

Correctness boundary:

```text
Never retarget the lease and never delete old lowerdirs until mountinfo proves
that workspace_root is now mounted with the requested compact lowerdir list.
```

Important code paths:

- `staged_remount_overlay`
- `mount_overlay_for_verified_remount`
- `remount_verification_report`
- `overlay_mount_verified`
- `mountinfo_lowerdir_verified`

The implementation intentionally uses the legacy overlay mount data string for
this narrow path because common kernels can hide lowerdir details for the newer
mount API. The live remount path needs exact lowerdir visibility before lease
retarget.

### LayerStack Snapshot Compaction For Remount

Main file:

- `crates/daemon/layerstack/src/service.rs`

`compact_snapshot_for_remount` rebuilds a manifest from the currently mounted
snapshot layer paths, projects that snapshot into a compact checkpoint, and
returns:

- compact manifest,
- compact layer path list,
- before layer count,
- after layer count.

The current live isolated-workspace remount path uses this full-snapshot
compaction. That means a mounted lower chain of depth `L` is replaced with one
compact lowerdir before lease retarget:

```text
[l4, n3, n2, n1] -> [C(l4,n3,n2,n1)]
```

This is the strongest lowerdir bound for the experimental isolated-workspace
path. It preserves the mounted snapshot contents, but it does not preserve the
logical head/parent split in the lease manifest.

### Leased-Parent Compaction Primitive

Main file:

- `crates/daemon/layerstack/src/stack/mod.rs`

`LayerStack::compact_leased_parent_for_remount` implements the logical
`[lease-head, compact-parent]` form:

```text
[l4, n3, n2, n1] -> [l4, C(n3,n2,n1)]
```

It verifies the lease manifest is a contiguous suffix of the active manifest,
builds a compact checkpoint for the lease parent layers, rewrites the lease
manifest to keep the protected head plus compact parent, rewrites the active
manifest, retargets the lease, and removes only unreferenced old parent layers.

This primitive is useful for the design case where we want a running lease head
to remain explicit while compacting the parent prefix. It is covered by unit
tests, but the current live isolated-workspace test path uses full snapshot
compaction instead.

### Lease-Aware Prefix/Suffix Gap Reclaim

Main files:

- `crates/daemon/layerstack/src/squash.rs`
- `crates/daemon/layerstack/src/lease_aware.rs`
- `crates/daemon/layerstack/src/stack/mod.rs`

There are two related mechanisms:

1. Ordinary squash segments around lease heads.
   - `segment_around_lease_heads` flushes unleased runs into checkpoint
     segments and keeps lease heads as hard boundaries.
   - Shape:

```text
[n6, n5, l4, n3, n2, n1]
-> [C(n6,n5), l4, C(n3,n2,n1)]
```

This applies when only the lease head is protected.

2. Lease-aware gap reclaim around all protected leased layers.
   - `plan_lease_aware_gaps` treats all currently leased layers as protected.
   - Unleased runs above, between, or below protected layers can be compacted
     into view or delta checkpoints.
   - Protected layers are retained exactly.

This applies when historical leases still pin old layers. It can reclaim
unleased gaps but cannot reclaim any layer that is still referenced by an
active lease.

## Example: Single Pinned Layer

Assume:

- base snapshot size is `B`,
- every rewrite payload is `S`,
- active chain is `[n6, n5, l4, n3, n2, n1]`,
- `l4` is the running command's visible lease head.

### Hard Protection

If the running lease still references `[l4, n3, n2, n1]` and we do not remount,
the parent prefix under `l4` cannot be deleted. Only the unleased suffix/top gap
above the lease can be compacted:

```text
before: B + 6S
after:  B + 5S

[n6, n5, l4, n3, n2, n1]
-> [C(n6,n5), l4, n3, n2, n1]
```

### Remount Normalization

If the command is safely quiesced and remounted, the mounted snapshot can be
retargeted to a compact lowerdir set. In the logical head-plus-parent form:

```text
before: B + 6S
after:  B + 3S

[n6, n5, l4, n3, n2, n1]
-> [C(n6,n5), l4, C(n3,n2,n1)]
```

In the current isolated-workspace live remount experiment, the mounted snapshot
is compacted even more aggressively:

```text
mounted lease: [l4, n3, n2, n1] -> [C(l4,n3,n2,n1)]
active cleanup then squashes the public head to a bounded chain
```

After the live lease releases, a final squash can collapse the retained mutable
payload to:

```text
B + S
```

## Benchmark And Test Results

The benchmark numbers in this section are from the local measurements captured
on 2026-06-17 in the remount compaction work. They are directional performance
evidence, not a portable SLA. Percentages exclude the immutable base workspace
snapshot `B` and measure retained mutable LayerStack payload only.

### Direct LayerStack Hard Protection vs Remount

| Scenario | Policy | Leases | B | Before - B | After While Leased - B | After Release - B | Depth | Time |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 6 x same 1 MiB, mounted l4 | Hard protection | 1 | 1,048,576 | 5,242,880 | 4,194,304 | 0 | 6 -> 5 | 0.011754917s |
| 6 x same 1 MiB, mounted l4 | Remount normalized | 1 | 1,048,576 | 5,242,880 | 2,097,152 | 0 | 6 -> 3 | 0.044615000s |
| 6 x same 16 MiB, mounted l4 | Hard protection | 1 | 16,777,216 | 83,886,080 | 67,108,864 | 0 | 6 -> 5 | 0.011886500s |
| 6 x same 16 MiB, mounted l4 | Remount normalized | 1 | 16,777,216 | 83,886,080 | 33,554,432 | 0 | 6 -> 3 | 0.043966166s |
| 12 x same 1 MiB, old lease pins mid parent | Hard protection | 2 | 1,048,576 | 11,534,336 | 8,388,608 | 0 | 12 -> 9 | 0.014396541s |
| 12 x same 1 MiB, old lease pins mid parent | Remount normalized | 2 | 1,048,576 | 11,534,336 | 6,291,456 | 0 | 12 -> 3 | 0.053167334s |

Summary:

- Same-file 1 MiB case: remount saved 2,097,152 bytes over `B`, 50.0% less
  retained mutable payload, at 3.80x LayerStack-only time cost.
- Same-file 16 MiB case: remount saved 33,554,432 bytes over `B`, 50.0% less
  retained mutable payload, at 3.70x LayerStack-only time cost.
- Two-lease 1 MiB case: remount saved 2,097,152 bytes over `B`, 25.0% less
  retained mutable payload, at 3.69x LayerStack-only time cost.

### Historical Lease Pressure

| Scenario | Leases | Protected Layers | B | Before - B | After Top-Gap Reclaim - B | After Release - B | Depth | Time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 20 x same 1 MiB, historical readers at v4/v8/v12 | 3 | 12 | 1,048,576 | 19,922,944 | 12,582,912 | 0 | 20 -> 13 | 0.016175792s |

This proves top-gap reclaim is useful even with old leases, but it cannot
reclaim storage that active historical leases still reference.

### Live Namespace Remount Timing

Live E2E traces include request dispatch, command quiesce/inspection, staged
overlay switch, mountinfo verification, lease retarget, active cleanup, and
process resume.

| Shape | Time | Dirs | Commands | Processes | Before - B | After - B | Reduction Over B |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| Historical leases released, same command running | 46 ms | 9 -> 2 | 1 | 2 | 1,049,749 | 131,352 | 87.5% |
| Large same-file rewrite, 9 x 1 MiB | 54 ms | 10 -> 2 | 1 | 2 | 8,390,275 | 1,048,856 | 87.5% |
| Matrix deep tree, 18 files x 3 rewrites | 46 ms | 56 -> 2 | 2 | 4 | 598,621 | 296,870 | 50.4% |
| Matrix medium-large, 10 files x 5 rewrites | 53 ms | 52 -> 2 | 4 | 8 | 2,629,618 | 656,623 | 75.0% |
| Matrix single hot file, 12 x 512 KiB | 49 ms | 14 -> 2 | 1 | 2 | 5,769,456 | 524,664 | 90.9% |
| Process tree plus private state | 46 ms | 19 -> 2 | 1 | 3 | 3,935,453 | 786,761 | 80.0% |
| Process fanout, 10 child loops | 49 ms | 25 -> 2 | 1 | 22 | 4,329,369 | 393,497 | 90.9% |
| Three commands over 12-file x 4-rewrite tree | 43 ms | 50 -> 2 | 3 | 6 | 892,604 | 296,189 | 66.8% |
| Two remountable commands | 42 ms | 13 -> 2 | 2 | 4 | 985,333 | 196,923 | 80.0% |

Observed live remount window in the measured suite: 38 ms to 63 ms for the
ordinary matrix rows above. The high-process fanout row stopped and resumed 22
processes while still completing the verified remount in tens of milliseconds.

### Real Concurrent Pip Install Test

Archived live command:

```bash
The original live remount suite was retired and archived out of the active
workspace. This section records the previously captured result only.
```

Latest captured result:

| Metric | Value |
| --- | ---: |
| Result | 1/1 passed, 128 filtered |
| Test runtime | 6.96s |
| Test-binary compile before run | 2.21s |
| Installed files | 786 |
| Install-ready time | 2,759 ms |
| Live remount operation time | 75 ms |
| Post-remount verification time | 385 ms |
| Layer dirs | 19 -> 2 |
| Manifest depth | 19 -> 1 |
| Remounted lowerdir count | 1 |
| Compacted snapshot layers | 19 |
| Process/quiesced count | 2 / 2 |
| LayerStack storage bytes | 1,772,814 -> 197,168 |
| Saved LayerStack bytes | 1,575,646 |
| Storage reduction | 88.88% |

Important interpretation:

- The `19` layers are prebuilt public lower layers in the test setup.
- The real `pip install` creates private upperdir files inside the isolated
  session. It does not create those 19 LayerStack lower layers.
- The test proves a running command with a large private install tree can be
  quiesced, remounted, resumed, and then verify imports and installed file
  hashes after remount.

### Test Inventory

Captured live proof inventory:

| Test Filter | Result | Wall Time | Scope |
| --- | ---: | ---: | --- |
| `compact_remount_live_remount_preserves_concurrent_pip_style_install_tree` | 1/1 passed | 4.56s | pip-style private upperdir integrity |
| `compact_remount_live_remount` | 34/34 passed | 202.32s | early broad live remount filter |
| `coverage_goal2` | 16/16 passed | 143.09s | additional easy/medium/hard matrix |
| `coverage_goal3` | 20/20 passed | 184.79s | sparse, large, high-command, pinned-history live proof |
| `coverage_goal4` | 30/30 passed | 262.59s | final live proof batch with real pip and hard cases |
| `compact_remount_live_remount` | 100/100 passed | 802.03s | broad direct live proof over full inventory |
| `compact_remount_live_remount` after fallback removal | 100/100 passed | 789.36s | broad live proof with pressure-only blocked path |

The final test inventory was 100 compact-remount matching tests:

- 40 easy cases,
- 30 medium cases,
- 30 hard cases.

The live test shapes cover:

- same-file hot rewrites,
- large files up to multi-MiB cases,
- many small files,
- sparse trees,
- nested trees,
- process fanout,
- multiple remountable commands,
- historical leases,
- repeated remount cycles,
- private upperdir state,
- pip-style install trees,
- real concurrent local pip installs.

## Correctness Findings

1. Live remount can preserve a running command if the command is explicitly
   marked remountable and the process group passes all inspection checks.
2. The implementation does not rely on intent. It requires mountinfo lowerdir
   verification before lease retarget.
3. The blocked path does not run hard-protection fallback compaction. It reports
   storage pressure and leaves lowerdirs untouched.
4. Historical leases remain a hard correctness boundary. Remounting the newest
   lease cannot reclaim layers still referenced by older leases.
5. Layer count is not the same as storage pressure. Wide sparse trees can reduce
   directory count while adding a compact checkpoint that temporarily increases
   base-subtracted mutable bytes. Rewrite density and byte pressure must be
   part of production policy.

## Production Wiring Recommendation

The next production slice should not directly call live remount from every
publish. It should introduce a bounded policy gate:

```text
on publish/finalize:
    update LayerStack storage metrics
    if active depth or unsquashed bytes exceed threshold:
        if no active command lease:
            run ordinary squash/reclaim
        else if all commands for the lease are remountable and safe:
            run verified live remount normalization
        else:
            emit lease_remount_blocked pressure report
```

Recommended thresholds:

- Keep `auto_squash_max_depth`, but use a lower default than 100.
- Add `auto_squash_max_unsquashed_bytes`.
- Add a large-rewrite trigger for individual large layer payloads.
- Track and report lease age, pinned bytes, and parent-prefix bytes.

Required production invariants:

- Never delete lowerdirs from a running lease without verified mount switch and
  successful lease retarget.
- Always resume a stopped process group on error.
- Treat unknown process inspection state as blocked.
- Emit structured pressure telemetry for blocked sessions.
- Keep lease release cleanup as the final reclaim opportunity.

## Remaining Risks

- The production auto-squash path is not wired to live remount yet.
- Live remount currently depends on Linux `/proc`, process groups, and overlay
  mountinfo behavior.
- The remount-safe contract is explicit opt-in. Commands not marked
  `remountable` are blocked.
- Processes with cwd/root/open fd/mmap references inside the workspace are
  intentionally not remounted.
- Historical leases still require separate lease lifetime policy, because live
  remount of one lease cannot reclaim storage pinned by another active lease.

## Verification Commands To Reproduce

LayerStack unit and benchmark surface:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p layerstack
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -q -p layerstack --release --example bench_layerstack_gap_reclaim
```

Archived focused live real-pip proof:

```bash
The original live remount suite was retired and archived out of the active
workspace. Keep this as historical evidence rather than an active command.
```

Archived broad live compact-remount proof:

```bash
The original live remount suite was retired and archived out of the active
workspace. Keep this as historical evidence rather than an active command.
```

Report generation note: this document records the previously captured benchmark
evidence and current code inventory. Heavy benchmark and live E2E commands were
not rerun while writing this report.
