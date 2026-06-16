# LayerStack Command Lease Live Remount Spec

Date: 2026-06-17

## 1. Purpose

This spec defines the production policy for reducing LayerStack storage when a
command lease is live. It extends the leased-layer gap compaction design with a
safe command/session lifecycle protocol.

Policy:

```text
Normalize before launch by default.
Live remount only after quiesce and verification.
Never delete old lowerdirs based on intent.
Delete only after verified mount switch, lease retarget, and refcount check.
```

## 2. Problem

LayerStack can compact unleased gaps around leased layers, but a running command
lease at version `l4` is not just the logical layer `l4`. The running overlay
mount may still reference all lowerdirs needed to render that snapshot:

```text
active: [n6, n5, l4, n3, n2, n1]
lease:        [l4, n3, n2, n1]
```

If the system deletes `n3,n2,n1` before the running mount has moved to a compact
equivalent parent, the command may see broken paths, stale detached mounts, or a
split between old open descriptors and new path lookups.

Therefore live parent-prefix compaction is safe only when the mount transition
is verified before lease metadata is retargeted and old layers are reclaimed.

## 3. Goals

1. Keep new command leases bounded by normalizing before command launch.
2. Allow live remount only when the command session can be safely quiesced and
   inspected.
3. Fall back to hard protection when live remount cannot be proven safe.
4. Preserve correctness for cwd, root, open file descriptors, mounted lowerdirs,
   and command upperdir writes.
5. Report blocked remounts with actionable pressure metadata.
6. Verify storage reduction with live E2E experiments before enabling live
   remount by default.

## 4. Non-Goals

1. Do not require live remount for correctness. It is an optimization.
2. Do not delete a layer directory while any active manifest or live lease still
   references it.
3. Do not retarget lease metadata before the running mount switch is verified.
4. Do not attempt best-effort deletion after uncertain process inspection.
5. Do not remount arbitrary commands by default in the first implementation.

## 5. Terminology

| Term | Definition |
| --- | --- |
| Command lease | A LayerStack lease associated with an active command/session. |
| Lease manifest | The ordered layer list protected by a lease, newest-first. |
| Lease head | The first layer in the lease manifest, for example `l4`. |
| Parent prefix | The lower layers under the lease head, for example `n3,n2,n1`. |
| Compact parent | A checkpoint layer representing the parent prefix, for example `C(n3,n2,n1)`. |
| Hard protection mode | A fallback where every layer in the current lease manifest remains protected. |
| Live remount | Replacing the mounted overlay lowerdir stack while the session remains alive. |
| Quiesce | Freezing or stopping the command process group before inspection and remount. |
| Remount verified | Mountinfo and runtime checks prove the session now uses the expected new lowerdirs. |

LayerStack manifests and overlay lowerdirs are newest-first.

## 6. Two-Tier Policy

### 6.1 Tier 1: Normalize Before Launch

New commands should not start from an unbounded lowerdir chain. Before acquiring
or mounting a command snapshot:

1. Read active depth and retained unsquashed bytes.
2. If the active chain exceeds policy limits, compact to a bounded generation.
3. Acquire the command lease after compaction.
4. Mount the command overlay from the bounded lease.

This is the default path because no running process has to be moved.

### 6.2 Tier 2: Live Remount With Quiesce

Already-running commands may be normalized only through a verified live remount
protocol. This path is optional and conservative. If any step is uncertain, the
system must abort the live remount and fall back to hard protection mode.

## 7. Live Remount Protocol

For a running command lease:

```text
1. Mark lease as remount_pending.
2. Stop/freeze the command process group.
3. Inspect all processes in the session:
   - cwd
   - root
   - open fds
   - mapped files if practical
   - mountinfo
4. If any process is pinned to the workspace mount in a way we cannot safely move:
   reject live remount and fall back to hard-protection compact.
5. Build compact parent checkpoint C(parent).
6. Mount the new overlay at a staging mountpoint.
7. Verify the staged overlay before touching `workspace_root`.
8. Move the old `workspace_root` mount to a rollback mountpoint.
9. Move the staged overlay mount onto `workspace_root`.
10. Restore hidden-path masks.
11. Verify mountinfo/lowerdirs match expected [l4, C(parent)].
12. Unmount the rollback mount that still references old lowerdirs.
13. Retarget lease metadata.
14. Delete old parent layers only after refcount check.
15. Resume process group.
```

### 7.1 Required Ordering

The ordering is part of the correctness contract:

```text
build compact parent
verify staged mount
switch workspace_root
verify visible remount
unmount rollback mount
retarget lease
refcount-check deletion
resume command
```

Do not reorder this to retarget first. Metadata retarget is only a record of a
verified mount state.

### 7.2 Process Inspection

The quiesced process group must be inspected before remount. The first
implementation should treat uncertain inspection as blocked.

Block live remount when:

- a process cwd is inside the current workspace mount,
- a process root is inside the current workspace mount,
- an open fd points into the current workspace mount,
- a mapped file points into the current workspace mount,
- mountinfo cannot be read or parsed,
- process membership changes during inspection,
- freeze/stop fails or times out.

The initial policy may allow a stricter version:

```text
If any process is still alive in the session and not explicitly marked
remount-safe, block live remount.
```

### 7.3 Mount Switch

The desired target is:

```text
old lease lowerdirs: [l4, n3, n2, n1]
new lease lowerdirs: [l4, C(n3,n2,n1)]
```

The production implementation must stage the new overlay mount before switching
`workspace_root`, so mount creation or staged-probe failure leaves the existing
mount intact.

Required staged switch sequence:

```text
mount new overlay at staging
probe staging
move workspace_root -> rollback
move staging -> workspace_root
restore hidden-path masks
probe workspace_root
unmount rollback
report remount_staged_switch=true only after rollback unmount succeeds
```

If the final visible probe or rollback unmount fails, the implementation should
restore the old mount when possible and report the remount as unverified. Lease
retarget and layer deletion must not run for a partial switch.

Acceptable switch implementations must prove one of:

1. `workspace_root` now resolves to the new overlay mount, and old path lookups
   cannot continue through the old mount.
2. any old detached mount is still valid for live fds, and old lowerdir paths
   are not deleted until those fds are gone.

The first implementation should target option 1 and reject uncertain cases.

### 7.3.1 Maintenance Access To Hidden Daemon Paths

Some isolated workspaces mask daemon-owned paths such as `/eos` inside the
holder mount namespace after the initial overlay is mounted. A live remount still
needs to open daemon-owned lowerdir, upperdir, and workdir paths to build the
replacement overlay.

The remount helper may temporarily reveal those configured hidden paths only
inside the quiesced remount maintenance window:

```text
setns(holder user+mount namespace)
unmask configured daemon paths
open/mount compact overlay inputs
restore masks
verify mount/probe
retarget lease
resume process group
```

This maintenance window is valid only if every process that could observe the
holder namespace has been quiesced or the workspace is idle. If mask restore
fails, do not report the remount as verified and do not resume the command as a
successful remount.

### 7.4 Verification

After the switch, verify:

- `mountinfo` for `workspace_root` identifies an overlay mount,
- lowerdirs equal the expected compact list, newest-first,
- `upperdir` and `workdir` match the session binding,
- the staged mount was verified before the visible switch,
- the old rollback mount was unmounted before lease retarget,
- the command lease manifest can be retargeted to those exact lowerdirs,
- a read probe through the mount returns expected bytes,
- a write probe, if allowed by policy, lands in the existing session upperdir.
- configured hidden-path masks have been restored before process resume.

Only after verification may the daemon retarget the lease metadata.

The implementation must report lowerdir proof separately from the general mount
switch proof. `mount_verified=true` means the staged switch, visible overlay
mount, rollback cleanup, configured probes, and exact lowerdir proof succeeded.
When `mountinfo` exposes lowerdirs, `remount_mountinfo_lowerdir_verified` must
be `true` before treating the exact lowerdir check as satisfied. When the kernel
hides lowerdirs, the field must be `null` and live retarget must fail closed
before lease metadata is changed.

For staged live remounts, the remount helper should prefer the validated legacy
overlay mount path over the new mount API because common kernels can hide
`lowerdir` for new-mount API overlays. The legacy path is acceptable here only
because it is narrow, still opens and validates every lower/upper/work
directory, and exposes `lowerdir=` in mountinfo for exact verification.

## 8. Fallback Contract

Fallback is mandatory:

```text
if live_remount_verified:
    [n6,n5,l4,n3,n2,n1] -> [C(n6,n5), l4, C(n3,n2,n1)]
else:
    [n6,n5,l4,n3,n2,n1] -> [C(n6,n5), l4, n3, n2, n1]
```

In fallback mode:

1. Do not retarget the running lease.
2. Do not delete parent prefix layers `n3,n2,n1`.
3. Compact only unleased gaps outside the protected lease manifest.
4. Emit `lease_remount_blocked`.

For same-file rewrites of size `S`:

```text
verified live remount: B + 6S -> B + 3S -> B + 1S after release
fallback hard protect: B + 6S -> B + 5S -> B + 1S after release
```

## 9. State Machine

Command lease states:

| State | Meaning | Allowed Next |
| --- | --- | --- |
| `active` | Lease is mounted/running normally. | `remount_pending`, `released` |
| `remount_pending` | Lease is selected for live normalization. | `quiescing`, `remount_blocked`, `active` |
| `quiescing` | Process group is being stopped/frozen. | `inspecting`, `remount_blocked` |
| `inspecting` | Session process and mount state are being checked. | `building_compact_parent`, `remount_blocked` |
| `building_compact_parent` | Compact parent checkpoint is being created. | `mount_switching`, `remount_failed` |
| `mount_switching` | New overlay is being staged/switched. | `verifying_mount`, `remount_failed` |
| `verifying_mount` | Mount and lowerdirs are checked. | `retargeting_lease`, `remount_failed` |
| `retargeting_lease` | Lease metadata is changed to compact parent. | `gc_old_parent`, `remount_failed` |
| `gc_old_parent` | Old parent layers are reclaimed after refcount check. | `active` |
| `remount_blocked` | No live remount attempted; fallback compact may run. | `active` |
| `remount_failed` | Attempt failed after partial work. | `active` or command failure, depending on mount state |
| `released` | Command/session ended; lease may be cleaned normally. | terminal |

`remount_failed` must include enough detail to decide whether the command can be
resumed. If mount state is uncertain, prefer failing the command over deleting
old lowerdirs.

For isolated workspaces, `remount_pending` is persisted with the workspace
handle before command quiesce begins and is cleared only after the remount
attempt has either completed or fallen back to a blocked state. A daemon restart
that observes a pending handle must treat it as an interrupted workspace and
reap it through the normal persisted-handle cleanup path rather than assuming
the remount completed.

## 10. Metrics And Trace

Emit `lease_remount_planned`:

| Field | Meaning |
| --- | --- |
| `remount_state` | Persisted state while the attempt is selected; expected `remount_pending`. |
| `lease_id` | Lease being considered. |
| `manifest_version` | Lease manifest version. |
| `lease_layer_count` | Current lease manifest depth. |
| `parent_prefix_layer_count` | Count of layers below lease head. |
| `parent_prefix_bytes` | Bytes in the old parent prefix. |
| `active_depth_before` | Active manifest depth before work. |
| `active_storage_bytes_before` | LayerStack storage bytes before work. |

Emit `lease_remount_blocked`:

| Field | Meaning |
| --- | --- |
| `remount_state_at_start` | Persisted state before block handling; expected `remount_pending`. |
| `remount_state_after` | Persisted state after block handling; expected `active`. |
| `reason` | Stable reason code. |
| `lease_age_s` | Age of the live lease/session. |
| `active_commands` | Count of active command sessions protecting the lease. |
| `command_ids` | Command ids included in the inspection. |
| `process_group_ids` | Process groups selected for quiesce/inspection. |
| `process_count` | Count of inspected or expected processes. |
| `quiesced_process_count` | Processes observed stopped after quiesce. |
| `pinned_fd_count` | Open fds that blocked remount, if known. |
| `pinned_cwd_count` | Processes with cwd under workspace, if known. |
| `pinned_root_count` | Processes with root under workspace, if known. |
| `pinned_mapped_file_count` | Mapped files under workspace, if known. |
| `mountinfo_checked_count` | Processes whose mountinfo was checked. |
| `inspected` | Whether process/mount inspection completed. |
| `quiesce_attempted` | Whether the implementation attempted to stop/freeze the process group. |
| `resumed` | Whether the process group was resumed after block/failure. |
| `inspection_detail` | Optional diagnostic detail for the block reason. |
| `pinned_bytes` | Bytes still protected by the lease manifest. |
| `fallback_compacted_layers` | Count of unleased top/suffix layers compacted. |

Stable block reasons:

- `freeze_failed`
- `freeze_timeout`
- `process_group_unavailable`
- `process_membership_changed`
- `cwd_pinned_workspace`
- `root_pinned_workspace`
- `fd_pinned_workspace`
- `mapped_file_pinned_workspace`
- `mountinfo_unavailable`
- `mountinfo_mismatch`
- `remount_not_enabled`
- `session_not_marked_remountable`
- `unsupported_platform`

Emit `lease_remount_finished`:

| Field | Meaning |
| --- | --- |
| `remount_state_at_start` | Persisted state before verified remount work; expected `remount_pending`. |
| `remount_state_after` | Persisted state after verified remount work; expected `active`. |
| `mount_verified` | Staged switch, visible overlay mount, rollback cleanup, and probes succeeded. |
| `remount_mountinfo_lowerdir_expected_count` | Expected lowerdir count from the requested compact layer list. |
| `remount_mountinfo_lowerdir_count` | Lowerdir count parsed from mountinfo, if the kernel exposes it. |
| `remount_mountinfo_lowerdir_count_matched` | Whether parsed lowerdir count equals expected, or `null` when unavailable. |
| `remount_mountinfo_lowerdir_verified` | Whether parsed lowerdirs exactly match expected, or `null` when unavailable. |
| `compact_parent_layer_id` | New compact parent layer. |
| `old_parent_layers_removed` | Count of old parent layers deleted. |
| `bytes_added` | Compact parent bytes added. |
| `bytes_removed` | Old parent bytes removed. |
| `active_depth_after` | Active depth after active rewrite and top-gap reclaim. |
| `storage_bytes_after` | LayerStack storage bytes after GC. |
| `duration_s` | Total live remount duration. |

## 11. Implementation Phases

### Phase 1: Launch-Time Normalization

- Add command admission policy that checks depth and unsquashed bytes before
  acquiring a command lease.
- Compact active chain before launch when limits are exceeded.
- Verify new command starts from bounded lowerdirs.

Experiment:

- Build 50 same-file layers.
- Launch a new command.
- Assert command lowerdir depth is bounded before execution begins.
- Assert storage does not grow with command count except upperdirs.

### Phase 2: Conservative Blocked Live Remount

- Add lease state and trace surface for `remount_pending` and
  `lease_remount_blocked`.
- Implement inspection as blocked-by-default for running public/ephemeral
  commands.
- Run fallback hard-protection compaction.

Experiment:

- Start a long-running command at `l4`.
- Trigger remount policy.
- Assert live remount is blocked with a stable reason.
- Assert only top/suffix gaps compact.
- Assert no lease retarget occurred and old parent layers remain.

### Phase 3: Isolated Idle Remount

- Allow remount for isolated workspaces with no active command process.
- Reuse existing namespace remount plumbing.
- Verify mountinfo and lowerdir list before lease retarget.

Experiment:

- Open isolated workspace.
- Ensure it is idle.
- Normalize/remount from `[l4,n3,n2,n1]` to `[l4,C(parent)]`.
- Assert old parent layers are reclaimed and workspace reads are unchanged.

### Phase 4A: Conservative Live Quiesce Inspection

- Add process group freeze/stop and resume.
- Inspect cwd/root/fds/mountinfo.
- Keep remount blocked whenever inspection is uncertain.

Experiment:

- Start a long command whose cwd is inside the workspace.
- Trigger remount policy.
- Assert the process group is stopped, inspected, and resumed.
- Assert live remount is blocked with `cwd_pinned_workspace`.
- Assert `lease_remount_blocked` reports process count, quiesced count,
  process group ids, pinned cwd count, and `resumed=true`.

### Phase 4B: Verified Live Retarget

- Add staging mount creation for the compact parent checkpoint.
- Atomically switch `workspace_root` to the staged overlay.
- Verify mountinfo/lowerdirs before lease metadata retarget.
- Delete old parent layers only after refcount reaches zero.

Experiment:

- Long command with cwd outside workspace and no workspace fds open.
- Freeze, inspect, remount, verify, retarget, GC, resume.
- Assert command continues and sees consistent reads/writes.

### Phase 5: Negative Live Remount E2E

- Prove unsafe cases fall back.

Experiments:

- Command cwd is workspace root.
- Command has open fd to a workspace file.
- Command changes process membership during inspection.
- `mountinfo` verification intentionally mismatches expected lowerdirs.

For branches that are scheduler- or kernel-race dependent, deterministic
test-only fault injection is acceptable for the first implementation if it runs
the normal quiesce/resume path first and returns through the same
`lease_remount_blocked` response and trace path as an organic failure.

Success:

- All cases emit `lease_remount_blocked`.
- No lease retarget occurs.
- Old parent layers remain.
- Top/suffix gap compaction may still run.

## 12. Required Tests

1. `launch_normalizes_before_command_mount`:
   - New command starts from bounded lowerdirs.

2. `running_command_remount_blocked_falls_back_to_hard_protection`:
   - Live remount blocked.
   - Active becomes `[C(top), l4, n3, n2, n1]`.
   - Lease remains `[l4, n3, n2, n1]`.

3. `isolated_idle_remount_retargets_after_mount_verification`:
   - Remount succeeds only after verification.
   - Lease becomes `[l4, C(parent)]`.
   - Old parent layers are removed.

4. `retarget_never_runs_before_mount_verification`:
   - Inject mount verification failure.
   - Assert lease manifest is unchanged.

5. `old_parent_layers_not_deleted_until_refcount_zero`:
   - Retarget one lease while another lease still references an old parent.
   - Assert shared old layer remains.

6. `live_remount_preserves_upperdir_writes`:
   - Write before remount.
   - Remount.
   - Write after remount.
   - Assert both writes are visible through the session upperdir.

7. `live_remount_negative_open_fd_blocks`:
   - Keep an fd open to a workspace file.
   - Assert remount is blocked and fallback compaction runs.

## 13. Open Questions

1. Should public/ephemeral commands ever be marked remountable, or should live
   remount be isolated-only until a stronger proof exists?
2. Can we use cgroup freezer reliably in every target runtime, or do we need a
   signal-based fallback?
3. How much fd inspection is enough for correctness across Linux kernels and
   container runtimes?
4. Should a failed remount always fail the command, or can we resume if the old
   mount is proven intact?
5. Should compact parent checkpoints be content-addressed/deduplicated across
   multiple leases with the same parent prefix?

## 14. Verdict

The production solution is two-tiered:

```text
New command: normalize before launch.
Running command: live remount only with quiesce and verification.
Uncertain command: hard-protection fallback.
```

This keeps storage pressure bounded for new work immediately, while making live
remount a measured optimization that cannot corrupt a running command or delete
lowerdirs still needed by a live mount.
