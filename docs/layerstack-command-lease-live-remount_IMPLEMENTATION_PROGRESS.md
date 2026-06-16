# LayerStack Command Lease Live Remount Implementation Progress

Date: 2026-06-17

Spec: `docs/layerstack-command-lease-live-remount_SPEC.md`

## 1. Current Verdict

Phase 5C is implemented as a conservative experimental path and is ready for
default-enablement design work outside explicitly remountable isolated commands.

Implemented and verified:

- Phase 1 default path: new command and isolated workspace admission normalize to
  a bounded snapshot before mounting/launching.
- Phase 2 conservative path: a running isolated command blocks live remount,
  emits `lease_remount_blocked`, and keeps the command running.
- Phase 3 experiment hook: an idle isolated workspace can compact/remount via the
  existing test hook and preserve public snapshot plus private upperdir state.
- Phase 4A conservative quiesce/inspection: a running command process group is
  stopped, inspected through `/proc`, resumed, and blocked with a concrete
  reason when it is pinned to the workspace.
- Phase 4B verified live retarget experiment: an explicitly remountable isolated
  command whose cwd/fds are outside the workspace is stopped, inspected,
  remounted in the holder mount namespace, verified by mountinfo plus read
  probe, lease-retargeted, squashed, resumed, and then proves post-remount
  reads/writes through the workspace root.
- Phase 4C staged switch hardening: the replacement overlay is mounted and
  probed at a private staging mountpoint, the old visible workspace mount is
  moved to rollback, staging is moved to `workspace_root`, hidden-path masks are
  restored, the visible mount is probed, rollback is unmounted, and only then may
  lease retarget plus squash run.
- Phase 4D persisted remount state: isolated workspace handles persist
  `remount_state=remount_pending` before quiesce/remount work begins, clear back
  to `active` after success or conservative block, and emit the state transition
  in remount traces.
- Phase 4E lowerdir proof telemetry: remount reports now distinguish staged
  mount/probe verification from exact `mountinfo` lowerdir proof. When the
  kernel exposes lowerdirs, the response and traces report whether count and
  exact newest-first paths match the requested compact layer list; when the
  kernel hides lowerdirs, those proof fields remain `null` instead of being
  implied by `mount_verified=true`.
- Phase 4F strict lowerdir proof gate: staged live remount now uses the
  validated legacy overlay mount path so mountinfo exposes `lowerdir=...`, and
  both staging verification and final visible verification require exact
  newest-first lowerdirs before `mount_verified=true`, lease retarget, or
  parent-prefix reclamation.
- Phase 5A fd-pinned fallback: an otherwise remountable command with cwd outside
  the workspace but an open fd to a workspace file is quiesced, inspected,
  resumed, and blocked with `fd_pinned_workspace` without lease retarget.
- Phase 5B mapped-file fallback: an otherwise remountable command with cwd and
  fds outside the workspace but a mapped workspace file is quiesced, inspected,
  resumed, and blocked with `mapped_file_pinned_workspace` without lease
  retarget.
- Phase 5C forced unsafe fallback coverage: the test remount hook can force
  `process_membership_changed` or `mountinfo_mismatch` after normal quiesce
  inspection, then returns through the same `lease_remount_blocked` report path
  without lease retarget.

Still not production-complete:

- production hardening for organic process-membership races and kernel-level
  mountinfo mismatch failures beyond deterministic test fault injection,
- default enablement for arbitrary commands.

The important correction from the Phase 4B experiment is that live remount must
be verified in the holder/command mount namespace, not inferred from daemon
metadata. Two failed live runs proved this:

1. The helper originally remounted outside the holder namespace and falsely
   reported success.
2. After adding `setns`, the helper could not see `/eos/state` because the
   holder namespace had the configured `/eos` mask applied after initial mount.

The current experiment fixes both by requiring `setns_user_mnt`, temporarily
unmasking hidden paths only during the quiesced remount maintenance window,
restoring the mask before verification/resume, requiring a namespace-local read
probe, and requiring staged-switch plus rollback-unmount telemetry before lease
metadata retarget. Phase 4D additionally persists the selected remount state
before quiesce, so a daemon restart can distinguish an interrupted remount from
a normal active handle and reap it through persisted-handle cleanup. Phase 4E
makes the exact lowerdir proof level explicit so operators can see whether the
kernel exposed enough mountinfo to prove equality. Phase 4F switches the
remount helper to the validated legacy mount path for staged remounts and
requires exact lowerdir equality before retarget. Phase 5A adds live coverage
for fd-pinned commands that are otherwise explicitly remountable, and Phase 5B
adds the same conservative fallback coverage for mapped workspace files. Phase
5C covers the remaining specified negative branches with deterministic
test-fault injection that still runs normal quiesce/resume and returns through
the production blocked-remount report path.

## 2. Files And API Changes

### LayerStack

- `crates/daemon/layerstack/src/service.rs`
  - Added `SnapshotNormalization`.
  - Added `CommandSnapshot`.
  - Added `acquire_bounded_snapshot_for_command(root, request_id, max_depth)`.
  - Shapes copy-through outcomes into command-admission telemetry.

- `crates/daemon/layerstack/src/stack/mod.rs`
  - Added/used `BoundedCommandSnapshot`.
  - Added `LayerStack::acquire_bounded_snapshot_for_command`.

- `crates/daemon/layerstack/examples/bench_layerstack.rs`
  - Added launch-normalization benchmark rows.
  - Added remount-compaction exhaustive scenarios.

- `crates/daemon/layerstack/examples/bench_layerstack_gap_reclaim.rs`
  - Added gap-reclaim experiments for protected `l4` and normalized live-lease
    model.

- `crates/daemon/layerstack/tests/unit/service.rs`
  - Added `acquire_bounded_snapshot_for_command_normalizes_before_lease`.

### Command Launch

- `crates/daemon/command/src/process.rs`
  - Added `CommandProcess::process_group_id()` so operation-layer remount
    inspection can quiesce the whole command session rather than a single pid.

- `crates/daemon/operation/src/command/service/exec.rs`
  - Command launch now calls `service::acquire_bounded_snapshot_for_command`.
  - Emits `command_snapshot_normalized` with depth, checkpoint, removed layer,
    pinned byte, and lease layer counts.

- `crates/daemon/operation/src/command/service/remount.rs`
  - Added `CommandRemountInspection`.
  - Added `CommandRemountQuiesce`, a scoped guard that keeps process groups
    stopped across live remount and resumes on `finish`, explicit `resume`, or
    `Drop`.
  - Added `CommandOps::inspect_live_remount_for_caller(caller_id)`.
  - Added `CommandOps::begin_live_remount_for_caller(caller_id)` for the
    verified-remount path.
  - On Linux, active isolated commands are inspected by process group:
    `SIGSTOP`, wait for stopped members, inspect `/proc/*/{cwd,root,fd,maps,mountinfo}`,
    then `SIGCONT`.
  - Blocks with concrete stable reasons including `process_group_unavailable`,
    `freeze_failed`, `freeze_timeout`, `process_membership_changed`,
    `cwd_pinned_workspace`, `root_pinned_workspace`, `fd_pinned_workspace`,
    `mapped_file_pinned_workspace`, `mountinfo_unavailable`,
    `mountinfo_mismatch`, and `unsupported_platform`.
  - Reports process count, quiesced count, process group ids, pinned counts,
    mountinfo check count, inspection status, quiesce status, resume status, and
    optional detail.

- `crates/daemon/operation/src/command/service.rs`
  - Re-exported `CommandRemountInspection`.

- `crates/daemon/operation/src/command/mod.rs`
  - Re-exported `CommandRemountInspection` at the command module boundary.

- `crates/daemon/operation/src/command/contract.rs`
  - Added `cwd: Option<PathBuf>` and `remountable: bool` to
    `ExecCommandInput`.
  - Parses top-level `cwd` and `remountable` fields for explicit live-remount
    experiments.

- `crates/daemon/command/src/contract.rs`
  - Added `cwd` and `remountable` fields to `StartCommand`.

- `crates/daemon/core/src/op_adapter/command.rs`
  - Threads `cwd` and `remountable` into command start.
  - Keeps ephemeral/public commands non-remountable.

- `crates/daemon/core/src/runtime/workspace.rs`
  - Threads optional `test_force_block_reason` through the test remount hook.
  - Runs normal quiesce inspection first, resumes the process group, stamps the
    forced unsafe reason, and returns through `blocked_remount_report_for_test`
    without lease retarget.

- `crates/daemon/namespace/src/runner/fresh_ns/command.rs`
  - Allows an external absolute cwd only for explicitly remountable command
    requests. Default command cwd safety is unchanged.

### Isolated Workspace Runtime

- `crates/daemon/workspace/src/isolated_workspace/remount.rs`
  - Added `RemountProbe`.
  - Added `RemountOverlayReport`.
  - Added `RemountedWorkspace`.
  - Added staged-switch telemetry:
    `staged_switch`, `staging_verified`, `rollback_unmounted`, and
    `rollback_unmount_error`.
  - Added lowerdir proof telemetry:
    `mountinfo_lowerdir_expected_count`,
    `mountinfo_lowerdir_count_matched`, and `mountinfo_lowerdir_verified`.
  - Added parser unit coverage for staged-switch cleanup telemetry.

- `crates/daemon/workspace/src/isolated_workspace/manager/mod.rs`
  - Added `WorkspaceRemountState::{Active, Pending}` to `WorkspaceHandle`.

- `crates/daemon/workspace/src/isolated_workspace/manager/lifecycle.rs`
  - Added `mark_remount_pending(caller_id)` and
    `clear_remount_pending(caller_id)`.
  - These transitions update `last_activity` and persist `manager.json`.

- `crates/daemon/workspace/src/isolated_workspace/manager/recovery.rs`
  - Persists `remount_state` for every open isolated workspace handle.

- `crates/daemon/namespace/src/runner/setns.rs`
  - `remount_overlay` now calls `setns_user_mnt` before unmount/mount work.
  - Temporarily unmounts configured hidden-path masks during the quiesced
    remount maintenance window, restores them before verification, and keeps
    existing hidden-path behavior for resumed commands.
  - Replaced the previous in-place unmount/remount with a staged switch:
    mount/probe compact overlay at staging, move old `workspace_root` mount to
    rollback, move staging to `workspace_root`, restore hidden-path masks,
    verify the visible mount, and unmount rollback before reporting success.
  - Keeps the rollback mount available until mask restore and visible mount
    verification have succeeded, so a mask-restore or final-probe failure can
    restore the old mount before lease metadata is touched.
  - Emits a structured verification payload with mount namespace id,
    mountinfo fs type, optional lowerdir count, lowerdir expected-count and
    match status, read-probe status, and probe error.
  - Emits staged-switch telemetry and reports `mount_verified=false` for
    staging, visible-probe, or rollback-cleanup failures.
  - Added lowerdir proof helpers that return `null` when the kernel does not
    expose lowerdirs, `true` only when exposed values match the requested
    compact layer list, and `false` on exposed mismatches.
  - Staged live remount now uses the validated legacy overlay mount path so the
    kernel reports `lowerdir=` in mountinfo, and both staging and visible
    verification require exact lowerdir equality before success.

- `crates/daemon/overlay/src/kernel_mount.rs`
  - Added `move_mountpoint(source, target)` as a safe rustix wrapper around
    `move_mount` for staged live remounts.

- `crates/daemon/overlay/src/lib.rs`
  - Re-exported `move_mountpoint`.

- `crates/daemon/eosd/src/runner.rs`
  - `--remount-overlay` now returns the namespace runner's structured
    verification payload instead of a generic `ok` result.

- `crates/daemon/workspace/src/isolated_workspace/namespace/ns_runner.rs`
  - Parses the remount helper's `RunResult` stdout into `RemountOverlayReport`.

- `crates/daemon/workspace/src/isolated_workspace/manager/lifecycle.rs`
  - `IsolatedManager::remount_with_layers` now requires a verified
    `RemountOverlayReport` before updating handle layer metadata.

- `crates/daemon/core/src/runtime/workspace.rs`
  - `BoundState::acquire_snapshot(caller_id, max_depth)` now normalizes before
    acquiring an isolated workspace lease.
  - `WorkspaceEnterOutcome` carries `snapshot_normalization`.
  - Added `WorkspaceRemountCompactionAttempt::{Compacted, Blocked}`.
  - Added `WorkspaceRemountBlockedReport`.
  - `compact_remount_open_workspace_for_test(caller_id, root)` now binds runtime
    state from the injected root before deciding `not_open`, `blocked`, or
    compacted.
  - Active commands return `Blocked` with stable reason
    produced by command remount inspection.
  - Blocked reports now include lease id, manifest version, active command ids,
    process group ids, process/quiesce counts, cwd/root/fd/mapped-file pin
    counts, mountinfo check count, inspection status, resume status, lease age,
    lease depth, parent-prefix depth, parent-prefix bytes, and pinned bytes.
  - Compact reports now include live-remount verification facts:
    `remount_mount_namespace`, `remount_mountinfo_fs_type`,
    `remount_mountinfo_lowerdir_count`,
    `remount_mountinfo_lowerdir_expected_count`,
    `remount_mountinfo_lowerdir_count_matched`,
    `remount_mountinfo_lowerdir_verified`, `remount_probe_read_ok`,
    `remount_probe_content_matched`, `remount_probe_error`,
    `remount_staged_switch`, `remount_staging_verified`,
    `remount_rollback_unmounted`, and `remount_rollback_unmount_error`.
  - Test compact-remount now marks the caller handle `remount_pending` before
    command quiesce/remount work and clears it after success, block, or failure
    handling.

- `crates/daemon/core/src/op_adapter/isolation.rs`
  - Isolated enter emits `layer_stack.command_snapshot_normalized`.
  - Test compact-remount emits `layer_stack.lease_remount_planned` before
    `layer_stack.lease_remount_blocked`.
  - Blocked remount returns rejected kind `lease_remount_blocked`.
  - Trace/error payloads now include concrete quiesce/inspection fields instead
    of a generic active-command count.
  - Live remount success traces include namespace-local mount/probe verification
    facts and staged-switch cleanup facts before `lease_retargeted=true`.
  - Remount traces include `remount_state`, `remount_state_at_start`, and
    `remount_state_after`.
  - Remount traces now include lowerdir expected count, count match, and exact
    match proof fields.

- `crates/daemon/operation/src/isolation/contract.rs`
  - `IsolationTestCompactRemountInput` now parses `layer_stack_root`, so a
    non-open caller with isolation enabled returns `not_open` rather than
    uninitialized `feature_disabled`.
  - `IsolationTestCompactRemountInput` also parses optional `probe_path` and
    `probe_content` for live remount verification experiments.
  - `IsolationTestCompactRemountInput` parses optional
    `test_force_block_reason` as a closed test enum for
    `process_membership_changed` and `mountinfo_mismatch`.
  - `TestCompactRemountOutput` includes the remount verification and
    staged-switch cleanup fields, including lowerdir proof telemetry.

- `crates/daemon/core/src/runtime/services.rs`
  - Fixed config import to use `config::configs::daemon::CommandConfig`.

### Tests

- `crates/daemon/core/tests/unit/workspace_runtime.rs`
  - Added isolated enter normalization coverage.

- `crates/daemon/core/tests/unit/isolated_workspace/service.rs`
  - Updated enter trace expectations for `command_snapshot_normalized`.

- `crates/e2e-test/tests/workspace-runtime-isolated/isolated_workspace_compact_remount.rs`
  - Added live active-command blocked-remount E2E.
  - Added positive explicitly-remountable live remount E2E.
  - Tightened live blocked-remount assertions to require
    `cwd_pinned_workspace`, non-empty process group ids, non-zero process and
    quiesced counts, `inspected=true`, `quiesce_attempted=true`, and
    `resumed=true`.
  - Positive live E2E asserts `live_remount=true`, `mount_verified=true`,
    `lease_retargeted=true`, `process_resumed=true`, namespace-local read probe
    success, post-resume public read, and post-resume private upperdir write.
  - Positive remount E2E now also asserts `remount_staged_switch=true`,
    `remount_staging_verified=true`, and `remount_rollback_unmounted=true`.
  - Positive remount E2E now asserts the reported expected lowerdir count
    matches the remounted layer count, and requires count/exact lowerdir proof
    to be `true` before lease retarget.
  - Added fd-pinned negative live-remount E2E. The command opts into remount and
    runs with cwd outside the workspace, but holds an open fd to a workspace
    file; the remount attempt must block with `fd_pinned_workspace`.
  - Added mapped-file negative live-remount E2E. The command opts into remount,
    runs with cwd outside the workspace, maps a workspace file through direct
    `libc.mmap`, closes the fd, and must block with
    `mapped_file_pinned_workspace`.
  - Added forced process-membership and mountinfo-mismatch negative E2Es. Both
    run an otherwise remountable command with cwd/fds outside the workspace,
    force the unsafe reason through the test hook, and assert
    `lease_remount_blocked`, no layer-dir reclamation, `resumed=true`, and a
    matching trace reason.
  - Blocked-remount trace assertions now require `remount_state=remount_pending`
    on `lease_remount_planned` and `remount_state_after=active` on
    `lease_remount_blocked`.
  - Kept idle compact-remount and non-open negative-path coverage.

- `crates/daemon/namespace/tests/unit/runner/setns.rs`
  - Added Linux-only lowerdir proof helper coverage for unavailable, count-only,
    exact-match, and mismatch cases.

- `crates/daemon/workspace/tests/unit/isolated_workspace_sessions.rs`
  - Added `remount_pending_state_is_persisted_and_cleared`.

- `crates/e2e-test/test-reports/TEST-REPORT.md`
  - Appended live E2E iterations and artifact verdicts.

## 3. Benchmark Results

Commands:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

### 3.1 Retained Edit Growth

| Case | Layers | Payload Before | Payload After | Layer Dirs | Publish Time |
| --- | ---: | ---: | ---: | ---: | ---: |
| same file 1 MiB rewrite | 50 | 52,428,800 | 52,428,800 | 50 -> 50 | 1.729894 s |

Verdict: retained unsquashed same-file rewrites are `O(L * file_size)` until a
normalization/squash policy runs.

### 3.2 Launch Normalization

| Case | Layers | Payload Before | Payload After | Layer Dirs | Compact Time | Total Maintenance |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| new command, 50 x 1 MiB, max depth 16 | 50 | 52,428,800 | 1,048,576 | 50 -> 1 | 0.054249 s | 0.054249 s |
| new command with legacy lease pinning history | 50 | 52,428,800 | 53,477,376 | 50 -> 51 | 0.044758 s | 0.044758 s |

Verdict: new commands can start from a bounded `O(1)` lowerdir generation when no
old lease pins history. A legacy lease still pins old bytes, which is expected
and must be reported rather than silently hidden.

### 3.3 Squash Cost

| Case | Layers | Payload Before | Payload After | Peak Payload | Squash Time |
| --- | ---: | ---: | ---: | ---: | ---: |
| same file, 50 x 1 MiB | 50 | 52,428,800 | 1,048,576 | 53,477,376 | 0.035976 s |
| 1000 files x 1 KiB, 10 layers | 10 | 1,024,000 | 1,024,000 | 2,048,000 | 0.288835 s |
| 5000 files x 1 KiB, 10 layers | 10 | 5,120,000 | 5,120,000 | 5,120,000 | 1.282373 s |
| same file, 4 x 64 MiB | 4 | 268,435,456 | 67,108,864 | 335,544,320 | 0.017878 s |

Verdict: same-file large rewrite squash is fast in this benchmark because only
the final file version is materialized, but many-file squash is file-count
sensitive.

### 3.4 Remount Compaction Experiments

| Case | Payload Before | Payload After | Layer Dirs | Compact | Cleanup | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| same file 50 x 1 MiB, one open lease | 52,428,800 | 2,097,152 | 50 -> 2 | 0.031130 s | 0.037024 s | 0.068250 s |
| same file 50 x 1 MiB, five open leases | 52,428,800 | 2,097,152 | 50 -> 2 | 0.033576 s | 0.039743 s | 0.073594 s |
| rotating 5 files, 50 x 1 MiB, five leases | 52,428,800 | 10,485,760 | 50 -> 2 | 0.055215 s | 0.042661 s | 0.098178 s |
| hot 1 MiB plus 50 unique 64 KiB files, three leases | 55,705,600 | 8,650,752 | 50 -> 2 | 0.047122 s | 0.061778 s | 0.109091 s |
| rewrite 5 files x 256 KiB each layer, two leases | 65,536,000 | 2,621,440 | 50 -> 2 | 0.086592 s | 0.108034 s | 0.194774 s |
| current plus 4 historical leases | 52,428,800 | 48,234,496 | 50 -> 46 | 0.031254 s | 0.028506 s | 0.059870 s |

Verdict: remount-style compaction can reduce active/live storage to a small
constant for current leases, but historical leases still block reclaim for the
older versions they protect.

### 3.5 Gap Reclaim Formula Experiment

| Case | Before | After | After Release | Depth | Duration | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| protect `l4` only | 6,291,456 | 3,145,728 | 1,048,576 | 6 -> 3 | 0.014038000 s | `6S -> 3S -> 1S` |
| mounted `l4` prefix hard protected | 6,291,456 | 5,242,880 | 1,048,576 | 6 -> 5 | 0.011443791 s | `6S -> 5S -> 1S` |
| normalized live lease parent prefix | 6,291,456 | 3,145,728 | 1,048,576 | 6 -> 3 | 0.043149500 s | `6S -> 3S -> 1S` |

Verdict: the target formula is achievable only after the live lease is
normalized to `[l4, C(parent)]`. Without verified remount/retarget, hard
protection must keep `[l4,n3,n2,n1]`.

## 4. Live E2E Results

Prior active-command block proof:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-live-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-blocked-5 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
```

Result:

- 24/24 tests passed.
- Runner duration: 33,634 ms.
- Suite duration: 30,768 ms.
- Prebuild duration: 2,358 ms.
- `max_parallel=1`.
- `container_weight_cap=10`.
- `daemon_logs_copied=2`.
- `removed_containers=2`.
- Runtime logs loaded
  `/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os-remount-experiment/config/prd.yml`.

Trace proof:

- `layer_stack.lease_remount_blocked`
  - `reason=session_not_marked_remountable`
  - `active_commands=1`
  - `lease_layer_count=7`
  - `active_leases_after=1`
- `layer_stack.command_snapshot_normalized` on isolated enter.
- `command.command_snapshot_normalized` on command launch.

Quiesce/inspection proof:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-quiesce-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-quiesce-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
```

Result:

- 24/24 tests passed.
- Runner duration: 33,933 ms.
- Suite duration: 30,955 ms.
- Prebuild duration: 2,496 ms.
- `max_parallel=1`.
- `container_weight_cap=10`.
- `daemon_logs_copied=2`.
- `removed_containers=2`.

Trace proof:

- `layer_stack.lease_remount_planned`
  - `lease_layer_count=7`
  - `parent_prefix_layer_count=6`
  - `parent_prefix_bytes=45`
  - `active_depth_before=7`
  - `active_storage_bytes_before=1534`
- `layer_stack.lease_remount_blocked`
  - `reason=cwd_pinned_workspace`
  - `active_commands=1`
  - `command_ids=["cmd_1"]`
  - `process_group_ids=[77]`
  - `process_count=2`
  - `quiesced_process_count=2`
  - `pinned_cwd_count=1`
  - `pinned_root_count=0`
  - `pinned_fd_count=0`
  - `pinned_mapped_file_count=0`
  - `mountinfo_checked_count=2`
  - `inspected=true`
  - `quiesce_attempted=true`
  - `resumed=true`
  - `lease_layer_count=7`
  - `parent_prefix_layer_count=6`
  - `pinned_bytes=54`
  - `active_leases_after=1`

Verified live-retarget proof:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-retarget-5 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
```

Result:

- 25/25 tests passed.
- Runner duration: 31,396 ms.
- Suite duration: 30,930 ms.
- Prebuild duration: 136 ms.
- `max_parallel=1`.
- `container_weight_cap=10`.
- `daemon_logs_copied=1`.
- `removed_containers=1`.
- Runtime logs loaded
  `/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os-remount-experiment/config/prd.yml`.

Trace proof for the positive live-remount command:

- `isolated_workspace.test_compact_remount_finished`
  - `live_remount=true`
  - `mount_verified=true`
  - `remount_mount_namespace="mnt:[4026532693]"`
  - `remount_mountinfo_fs_type="overlay"`
  - `remount_probe_read_ok=true`
  - `remount_probe_content_matched=true`
  - `remount_probe_error=null`
  - `remountable_commands=1`
  - `process_count=2`
  - `quiesced_process_count=2`
  - `pinned_cwd_count=0`
  - `pinned_fd_count=0`
  - `pinned_mapped_file_count=0`
  - `process_resumed=true`
  - `after_manifest_depth=1`
  - `after_layer_dirs=2`
  - `active_leases_after=1`
- `layer_stack.lease_remount_finished`
  - `lease_retargeted=true`
  - `compacted_snapshot_layers=7`
  - `remounted_layer_count=1`
  - `before_storage_bytes=1566`
  - `after_storage_bytes=588`

The resumed command then read `public-live-5` through the workspace root and
wrote `live-private` through the preserved isolated upperdir.

Staged live-retarget proof:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-staged-2 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
```

Result:

- 25/25 tests passed.
- Runner duration: 36,834 ms.
- Suite duration: 36,389 ms.
- Prebuild duration: 126 ms.
- `max_parallel=1`.
- `container_weight_cap=10`.
- `daemon_logs_copied=1`.
- `removed_containers=1`.

Trace proof for the positive live-remount command:

- `isolated_workspace.test_compact_remount_finished`
  - `live_remount=true`
  - `mount_verified=true`
  - `remount_staged_switch=true`
  - `remount_staging_verified=true`
  - `remount_rollback_unmounted=true`
  - `remount_rollback_unmount_error=null`
  - `remount_mount_namespace="mnt:[4026533509]"`
  - `remount_mountinfo_fs_type="overlay"`
  - `remount_probe_read_ok=true`
  - `remount_probe_content_matched=true`
  - `remountable_commands=1`
  - `process_count=2`
  - `quiesced_process_count=2`
  - `process_resumed=true`
  - `after_manifest_depth=1`
  - `after_layer_dirs=2`
- `layer_stack.lease_remount_finished`
  - `lease_retargeted=true`
  - `compacted_snapshot_layers=7`
  - `remounted_layer_count=1`
  - `before_layer_dirs=7`
  - `after_layer_dirs=2`
  - `before_storage_bytes=1566`
  - `after_storage_bytes=588`

Trace proof for the idle remount path:

- `live_remount=false`
- `mount_verified=true`
- `remount_staged_switch=true`
- `remount_staging_verified=true`
- `remount_rollback_unmounted=true`
- `remount_rollback_unmount_error=null`
- `compacted_snapshot_layers=11`
- `before_layer_dirs=11`
- `after_layer_dirs=2`

Persisted remount-state proof:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-pending-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
```

Result:

- 25/25 tests passed.
- Runner duration: 34,201 ms.
- Suite duration: 33,243 ms.
- Prebuild duration: 599 ms.
- `max_parallel=1`.
- `container_weight_cap=10`.
- `daemon_logs_copied=1`.
- `removed_containers=1`.

Trace proof:

- Blocked live command:
  - `lease_remount_planned.remount_state="remount_pending"`
  - `lease_remount_blocked.remount_state_at_start="remount_pending"`
  - `lease_remount_blocked.remount_state_after="active"`
  - `reason="cwd_pinned_workspace"`
  - `process_count=2`
  - `quiesced_process_count=2`
  - `resumed=true`
- Explicitly remountable live command:
  - `test_compact_remount_finished.remount_state_at_start="remount_pending"`
  - `test_compact_remount_finished.remount_state_after="active"`
  - `lease_remount_finished.remount_state_at_start="remount_pending"`
  - `lease_remount_finished.remount_state_after="active"`
  - `mount_verified=true`
  - `remount_staged_switch=true`
  - `remount_rollback_unmounted=true`
- Idle remount:
  - `remount_state_at_start="remount_pending"`
  - `remount_state_after="active"`
  - `mount_verified=true`
  - `remount_staged_switch=true`
  - `remount_rollback_unmounted=true`

Verdict: the live command remount gate is now evidence-based and staged. Unsafe
sessions are frozen, inspected, resumed, and blocked; explicitly remountable
sessions are frozen, inspected, mounted at staging, verified, switched into the
holder namespace, verified again, rollback-cleaned, retargeted, squashed,
resumed, and then validated by live command read/write behavior. Remount
attempts now also have an explicit persisted `remount_pending -> active` state
transition.

## 5. Verification

Focused checks during Phase 4B:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p operation remount --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p operation exec_parse_accepts_remountable_cwd_fields --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p namespace remount_overlay_requires_setns_payload --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p daemon compact_remount --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remounts_explicitly_remountable_command --no-default-features
```

Focused results:

- `operation remount --all-targets`: 3 matched tests passed.
- `operation exec_parse_accepts_remountable_cwd_fields --all-targets`: 1 matched
  test passed.
- `namespace remount_overlay_requires_setns_payload --all-targets`: compiles on
  this non-Linux host; the Linux-only test is gated with `#[cfg(target_os =
  "linux")]`.
- `daemon compact_remount --lib`: compiled daemon path; 0 matched tests.
- no-feature E2E compile path:
  `compact_remount_live_remounts_explicitly_remountable_command` passed.

Focused checks during Phase 4C:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p workspace isolated_workspace::remount --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p namespace runner::setns --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p namespace --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p workspace --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p daemon compact_remount --lib
```

Focused results:

- `workspace isolated_workspace::remount --lib`: 2 parser/summary tests passed.
- `namespace runner::setns --lib`: 3 matched tests passed.
- `namespace --all-targets`: 9 tests passed.
- `workspace --all-targets`: 17 tests passed.
- `daemon compact_remount --lib`: compiled daemon path; 0 matched tests.

Focused checks during Phase 4D:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p workspace remount_pending_state_is_persisted_and_cleared --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p workspace --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p daemon op_adapter::isolation --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_blocks_while_isolated_command_is_running --no-default-features
```

Focused results:

- `workspace remount_pending_state_is_persisted_and_cleared --all-targets`: 1
  matched test passed.
- `workspace --all-targets`: 18 unit tests passed.
- `daemon op_adapter::isolation --lib`: 5 matched tests passed.
- no-feature focused E2E compile path:
  `compact_remount_blocks_while_isolated_command_is_running` passed.

Focused checks during Phase 4E:

```bash
cargo fmt --check
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p namespace runner::setns --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p workspace isolated_workspace::remount --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p daemon op_adapter::isolation --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation --all-targets
```

Focused results:

- `cargo fmt --check`: passed.
- `namespace runner::setns --lib`: 3 matched host tests passed; the new
  lowerdir proof helper test is Linux-only and runs under Linux targets.
- `workspace isolated_workspace::remount --lib`: 2 remount parser/summary tests
  passed.
- `daemon op_adapter::isolation --lib`: 5 matched adapter tests passed.
- `operation --all-targets`: initially exposed a stale `/tmp/ephemeral-os-target`
  build artifact for the `command` crate; after
  `CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo clean -p command -p operation`,
  rerun passed with 61 unit tests, 4 checkpoint tests, and 1 contract test.

Focused checks during Phase 4F:

```bash
cargo fmt --check
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p namespace runner::setns --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_open_isolated_workspace_reclaims_old_lower_chain --no-default-features
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p operation --all-targets
```

Focused results:

- `cargo fmt --check`: passed.
- `namespace runner::setns --lib`: 3 matched host tests passed.
- no-feature focused E2E compile path:
  `compact_remount_open_isolated_workspace_reclaims_old_lower_chain` passed.
- `operation --all-targets`: 61 unit tests, 4 checkpoint tests, and 1 contract
  test passed.

Focused checks during Phase 5A:

```bash
cargo fmt --check
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_blocks_when_remountable_command_holds_workspace_fd --no-default-features
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p operation --all-targets
```

Focused results:

- `cargo fmt --check`: passed.
- no-feature focused E2E compile path:
  `compact_remount_blocks_when_remountable_command_holds_workspace_fd` passed.
- `operation --all-targets`: 61 unit tests, 4 checkpoint tests, and 1 contract
  test passed.

Focused checks during Phase 5B:

```bash
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_blocks_when_remountable_command_maps_workspace_file --no-default-features
```

Focused results:

- `cargo fmt`: passed.
- no-feature focused E2E compile path:
  `compact_remount_blocks_when_remountable_command_maps_workspace_file` passed.

Focused checks during Phase 5C:

```bash
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p operation test_force_block_reason --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_blocks_when_process_membership_changes_during_inspection --no-default-features
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_blocks_when_mountinfo_verification_mismatches --no-default-features
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p xtask -- package
```

Focused results:

- `cargo fmt`: passed.
- `operation test_force_block_reason --lib`: 2 parser unit tests passed.
- no-feature focused E2E compile paths:
  `compact_remount_blocks_when_process_membership_changes_during_inspection`
  and `compact_remount_blocks_when_mountinfo_verification_mismatches` passed.
- `cargo run -p xtask -- package`: passed after Phase 5C changes, packaged
  `dist/eosd-linux-amd64` with sha256
  `5539eb5bf396a1d4ccb29749a92f10b59f93a7b5c89dad7431e2d038e10158d1`.

Benchmark commands:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack_gap_reclaim
```

Live E2E commands:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-retarget-4 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-retarget-5 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-staged-2 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-pending-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-lowerdir-proof-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-retarget-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-strict-lowerdir-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-fd-blocked-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-map-blocked-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-map-blocked-2 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-forced-fallbacks-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-forced-fallbacks-2 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
```

Live E2E results:

- `live-remount-retarget-4`: 23/25 passed. This intentionally failed after
  adding `setns`, proving the target namespace could not see masked `/eos/state`
  lowerdirs.
- `live-remount-retarget-5`: 25/25 passed in 31,396 ms after the remount-only
  unmask/remask maintenance window.
- `live-remount-staged-2`: 25/25 passed in 36,834 ms after staged switch,
  mask-restore, visible-probe, and rollback-unmount verification.
- `live-remount-pending-1`: 25/25 passed in 34,201 ms after persisted
  `remount_pending -> active` trace verification.
- `live-remount-lowerdir-proof-1`: 25/25 passed in 34,343 ms after lowerdir
  proof telemetry was added. The live remount and idle remount traces reported
  `remount_mountinfo_lowerdir_expected_count=1`,
  `remount_mountinfo_lowerdir_count=null`,
  `remount_mountinfo_lowerdir_count_matched=null`, and
  `remount_mountinfo_lowerdir_verified=null` on the current kernel.
- `live-remount-strict-lowerdir-1`: 25/25 passed in 35,188 ms after switching
  staged remounts to the validated legacy overlay mount path and requiring exact
  lowerdir proof. Live and idle remount traces reported
  `remount_mountinfo_lowerdir_count=1`,
  `remount_mountinfo_lowerdir_expected_count=1`,
  `remount_mountinfo_lowerdir_count_matched=true`, and
  `remount_mountinfo_lowerdir_verified=true`.
- `live-remount-fd-blocked-1`: 26/26 passed in 36,567 ms after adding the
  fd-pinned negative case. The blocked trace reported
  `reason=fd_pinned_workspace`, `remountable_commands=1`,
  `pinned_fd_count=1`, `pinned_cwd_count=0`, `resumed=true`, and
  `active_leases_after=1`.
- `live-remount-map-blocked-1`: 26/27 passed in 38,958 ms after adding the
  initial mapped-file negative case. The helper used Python `mmap.mmap`, which
  still left a workspace fd visible; the blocked trace reported
  `reason=fd_pinned_workspace`, `pinned_fd_count=1`, and
  `pinned_mapped_file_count=1`.
- `live-remount-map-blocked-2`: 27/27 passed in 38,284 ms after switching the
  helper to direct `libc.mmap` and explicit fd close. The mapped-file blocked
  trace reported `reason=mapped_file_pinned_workspace`,
  `remountable_commands=1`, `process_count=3`, `quiesced_process_count=3`,
  `pinned_fd_count=0`, `pinned_mapped_file_count=1`,
  `mountinfo_checked_count=3`, `resumed=true`, and `active_leases_after=1`.
- `live-remount-forced-fallbacks-1`: 27/29 passed in 43,172 ms before packaging
  the updated daemon. The stale daemon ignored `test_force_block_reason`, so the
  two new forced-fallback tests incorrectly observed successful lease retarget.
- `live-remount-forced-fallbacks-2`: 29/29 passed in 40,773 ms after packaging
  the updated daemon. The forced mountinfo trace reported
  `reason=mountinfo_mismatch`, `process_count=2`, `quiesced_process_count=2`,
  `mountinfo_checked_count=2`, `inspected=true`, `resumed=true`,
  `before_layer_dirs=7`, `after_layer_dirs=7`, and `active_leases_after=1`. The
  forced membership trace reported `reason=process_membership_changed`,
  `process_count=2`, `quiesced_process_count=2`, `inspected=false`,
  `resumed=true`, `before_layer_dirs=7`, `after_layer_dirs=7`, and
  `active_leases_after=1`.

Final closeout verification:

```bash
cargo fmt
cargo fmt --check
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p layerstack
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo test -p operation --all-targets
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-staged-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-forced-fallbacks-2 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
git diff --check
```

Final closeout results:

- `cargo fmt`: passed.
- `cargo fmt --check`: passed.
- `layerstack`: 78 unit tests, 1 CAS fixture test, 16 stack integration tests,
  and doc tests passed.
- `operation --all-targets`: 63 unit tests, 4 checkpoint tests, and 1 contract
  test passed.
- `cargo run -p xtask -- package`: passed, packaged
  `dist/eosd-linux-amd64` with sha256
  `5539eb5bf396a1d4ccb29749a92f10b59f93a7b5c89dad7431e2d038e10158d1`.
- `live-remount-forced-fallbacks-2`: 29/29 live `workspace-runtime-isolated`
  E2E tests passed with cwd, fd, mapped-file, forced membership-change, forced
  mountinfo-mismatch, positive live-retarget, and idle remount coverage.
- `git diff --check`: passed.

## 6. Remaining Risks

1. The remount helper temporarily unmasks configured hidden paths in the holder
   namespace while commands are quiesced, then restores the mask before
   verification/resume. This is acceptable for the experiment, but production
   should either use a narrower maintenance-only access path or prove the
   quiesce boundary covers every process that can observe the namespace.
2. Phase 5 negative coverage now covers the specified branches, but two of them
   are deterministic fault-injection proofs. The live suite proves cwd-pinned,
   fd-pinned, and mapped-file-pinned sessions organically block, and it proves
   process-membership-change plus mountinfo-mismatch reporting through the
   blocked-remount path via `test_force_block_reason`. Production hardening
   should still attempt organic stress coverage for scheduler races and
   kernel-level mountinfo mismatches.
3. Restart recovery is intentionally conservative. `remount_pending` is durable
   in the isolated workspace handle file, but daemon restart does not resume an
   interrupted live remount; it treats persisted handles as interrupted
   workspaces and reaps them through the existing cleanup path.
4. Historical leases can still pin storage. The policy must continue reporting
   lease count, age, and pinned bytes so operators can distinguish expected
   protection from leaks.
5. `SIGSTOP`/`SIGCONT` quiesce is Linux-specific and coarse. A production path
   may still need a cgroup freezer or runtime-specific equivalent before
   enabling live retarget by default.
