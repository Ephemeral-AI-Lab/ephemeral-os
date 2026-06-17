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

## 7. Post-Closeout Stress Coverage: Larger Payloads, Multi-Lease Pins, Command Integrity

Additional focused coverage was added after the initial closeout to compare
hard-protection against live-remount normalization under heavier storage and
command-integrity shapes.

Files changed:

- `crates/daemon/layerstack/examples/bench_layerstack_gap_reclaim.rs`
  - Added 16 MiB same-file hard-protection and normalized-remount benchmark
    rows.
  - Added two-lease benchmark rows where an older lease pins part of a newer
    lease's parent prefix.
  - Added a three-lease benchmark row with 20 retained same-file layers and 12
    pinned lower layers.
  - Fixed the benchmark row helper so `lease_count` reports multi-lease rows
    accurately.
- `crates/daemon/layerstack/tests/stack.rs`
  - Added a 4 MiB same-file parent-prefix normalization integrity test.
  - Added a two-lease storage test proving parent-prefix normalization reclaims
    only layers not still pinned by an older lease.
  - Added a three-lease storage and read-integrity test proving a large
    unleased top gap can still reclaim while historical snapshots remain
    readable.
- `crates/e2e-test/tests/workspace-runtime-isolated/isolated_workspace_compact_remount.rs`
  - Added a live remountable command test that resumes after remount, hashes two
    64 KiB public files, verifies the hash in-command, and atomically writes the
    private hash result through the isolated upperdir.
  - Added a two-command live remount test with two remountable commands, twelve
    alternating 96 KiB public file writes, a child/subshell digest pipeline, and
    private post-remount integrity outputs from both commands.
  - Added a mixed safe-plus-fd-pinned command test proving one unsafe command
    blocks the whole remount without partial retarget while both commands resume.
  - Added a process-tree live remount test with 18 alternating 256 KiB public
    rewrites and command-created private upperdir state verified after remount.
  - Added a two-open-lease live remount test where an older isolated caller pins
    a historical 256 KiB snapshot while a newer caller runs a remountable command
    and live-remounts onto a compacted parent.
  - Added a repeated-cycle live remount test where one long-running remountable
    command is remounted three times while public writes move the active head
    between cycles and private upperdir state accumulates across cycles.
  - Added a many-file tree live remount test with 32 files x 3 retained
    rewrites, manifest-driven command hashing, post-remount public head
    movement, and isolated pinned-snapshot verification after resume.
  - Added a process-fanout live remount test with ten background child loops,
    24 x 192 KiB public rewrites, command-created private state, post-remount
    public head movement, and pinned-snapshot hash verification after resume.
  - Added a large same-file live remount test with nine 1 MiB rewrites, a
    command-side SHA-256 check, and post-remount public head movement.
  - Added a historical-release live remount test where three older leases pin a
    16-layer same-file chain, then release while the newest command remains
    running, allowing a second remount to reclaim to bounded dirs.
  - Added a three-command wide-tree live remount test with 12 files x 4
    rewrites, ordered/reverse/private-even hash checks, private upperdir state,
    and post-remount public head movement.
  - Added a three-open-lease live remount test with two historical readers, two
    remountable commands on the newest lease, bash plus Python chunked hashing,
    post-remount public head movement, and all three snapshots verified after
    newest-lease retarget.
  - Added six generated matrix live-remount cases through `RemountMatrixCase`:
    a 12 x 512 KiB hot-file chain, an 18-file x 3 deep tree with two commands,
    a 36-file x 2 many-tiny-file tree with three commands, a 10-file x 5
    medium/large tree with four commands, a 48-file sparse tree with two
    commands, and a 16-file x 4 nested tree with four commands.
  - Added twelve more single-lease matrix cases and three pinned-history matrix
    cases covering larger hot rewrites, wider trees, more commands, and up to
    four historical leases plus one live lease.
  - Added a concurrent pip-install-shaped live remount test. One remountable
    command builds a private `site-packages`-style tree with 384 concurrent
    module/resource pairs plus package metadata, waits in a live command
    session during remount, then recomputes the install-tree SHA-256 after
    resume and spot-checks installed module/resource files.
  - Added the 16-case `coverage_goal2` batch:
    - Easy: four single-lease matrix cases plus one pinned-history case covering
      micro wide manifests, small hot rewrites, nested trees, two-command
      balanced hashes, and two historical leases.
    - Medium: four single-lease matrix cases plus one pinned-history case
      covering 128 tiny files, 512 KiB file rewrites, 32-file/5-command
      hashing, 16 hot rewrites, and three historical leases.
    - Hard: four single-lease matrix cases plus two pinned-history cases
      covering one 8 MiB file rewritten four times, 64 files x 4 rewrites with
      eight commands, 192 sparse files, 1 MiB hot quad rewrites with six
      commands, and four historical leases.
  - Added the 20-case `coverage_goal3` batch:
    - Easy: eight sets covering small hot rewrites, 64/96-file sparse fanout,
      three-command hashing, and one older pinned reader.
    - Medium: six sets covering a 256-file tiny tree, 48 files x 3 rewrites,
      hot 512 KiB file pairs, 24 rewrites of one file, and three older pinned
      readers.
    - Hard: six sets covering the max-supported 8 MiB single-write file with
      five rewrites, eight 1 MiB files, 320 sparse files with eight commands,
      32 files x 5 rewrites with eight commands, and four historical readers.
      A first attempt at 16 MiB and 12 MiB single-write rows proved the
      operation-level `sandbox.file.write` cap is 8 MiB, so future larger-file
      stress must use multiple files or command-side private data creation.
  - Added the remaining 29 `coverage_goal4` rows after the real concurrent pip
    row, completing the 30-case final batch:
    - Easy: twelve sets covering small hot rewrites, sparse/nested trees,
      three-command fanout on small files, and one/two historical readers.
    - Medium: ten sets covering 64/128-file sparse trees, 16/32/48-file
      rewrite trees, hot 128 KiB pairs, 20 same-file rewrites, 256 KiB file
      groups, and two/three historical readers.
    - Hard: seven additional sets plus the existing real-pip row, covering
      1 MiB multi-file rewrites, one 4 MiB file rewritten eight times,
      256 sparse files with eight commands, 96 files x 4 rewrites with eight
      commands, four historical readers, and 64 files x 64 KiB with eight
      commands.
- `crates/e2e-test/test-reports/TEST-REPORT.md`
  - Added Iteration 23 with the first complex-command live E2E result.
  - Added Iterations 24 and 25 with the failed newline-format retry and the
    passing two-command live E2E result.
  - Added Iteration 26 with the mixed blocked-command and process-tree live E2E
    result.
  - Added Iterations 27 through 29 with the initial multi-lease harness failure,
    exact live retry, and passing full focused suite.
  - Added Iterations 30 and 31 with the exact repeated-cycle live test and
    passing full focused suite.
  - Added Iterations 45 through 47 with the six-case matrix non-live gate,
    exact live proof, and passing full focused suite.
  - Added Iterations 48 through 61 with the expanded matrix and pinned-history
    compile/live gates, the pinned-history assertion fix, scoped stale-container
    reap, the concurrent pip-style install tree exact live proof, and the broad
    34-test compact-remount live proof.
  - Added Iterations 62 through 64 with the `coverage_goal2` non-live gate,
    focused 16-test live proof, and broad 50-test non-live inventory check.
  - Added Iterations 65 through 72 with the `coverage_goal3` non-live gate,
    the 16 MiB/12 MiB write-limit failures, the corrected 8 MiB exact live
    proof, the passing 20-test live proof, and the broad 70-test non-live
    inventory check.
  - Added Iterations 73 through 78 with the real concurrent pip exact proof,
    `coverage_goal4` non-live/live gates, and the broad 100-test non-live
    inventory check.
- `docs/layerstack-hard-protection-vs-remount_PERFORMANCE_REPORT.md`
  - Added the concrete hard-protection compact versus verified remount
    performance report needed before deciding whether to remove blocked-path
    hard-protection fallback compaction.

Fresh benchmark command:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -q -p layerstack --release --example bench_layerstack_gap_reclaim
```

Fresh benchmark rows, recomputed over the base snapshot `B`:

| Case | Leases | B | Before - B | After While Leased - B | After Release - B | Depth | Duration |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `mounted_l4_prefix_large_file_view_reclaim` | 1 | 16,777,216 | 83,886,080 | 67,108,864 | 0 | 6 -> 5 | 0.011886500s |
| `mounted_l4_prefix_normalized_large_file_reclaim` | 1 | 16,777,216 | 83,886,080 | 33,554,432 | 0 | 6 -> 3 | 0.043966166s |
| `multi_lease_pinned_prefix_view_reclaim` | 2 | 1,048,576 | 11,534,336 | 8,388,608 | 0 | 12 -> 9 | 0.014396541s |
| `multi_lease_pinned_prefix_normalized_reclaim` | 2 | 1,048,576 | 11,534,336 | 6,291,456 | 0 | 12 -> 3 | 0.053167334s |
| `many_lease_deep_pinned_top_gap_reclaim` | 3 | 1,048,576 | 19,922,944 | 12,582,912 | 0 | 20 -> 13 | 0.016175792s |

Interpretation:

- Large same-file normalization retained 50% less mutable layer payload over
  `B` than hard protection while the lease was still held: `B + 32 MiB`
  versus `B + 64 MiB`, at roughly 44 ms versus 12 ms in this run.
- Multi-lease normalization retained 25% less mutable layer payload over `B`
  than hard protection while both leases were held: `B + 6 MiB` versus
  `B + 8 MiB`. It
  could not reach the ideal `B + 3 MiB` because the older lease still pinned
  4 MiB of historical parent layers.
- The two-lease unit test verifies that the older lease can still read version 4,
  the normalized middle lease can still read version 8, and the active stack can
  still read version 12 after parent-prefix reclaim and top-gap reclaim.
- The three-lease unit test verifies historical readers at versions 4, 8, and
  12 while reclaiming the unleased top gap from versions 13 through 20. Storage
  remains proportional to the pinned historical set until those leases release.

Fresh verification:

```bash
cargo fmt
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p layerstack lease_aware -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount_preserves_complex_command_integrity --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remounts_multiple_remountable_commands_consistently --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_blocks_mixed_safe_and_fd_pinned_remountable_commands --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount_preserves_process_tree_and_private_state --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-complex-integrity-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-multi-command-2 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-mixed-tree-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount_preserves_concurrent_pip_style_install_tree -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated coverage_goal2 --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated coverage_goal2 -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated coverage_goal3 --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount_coverage_goal3_hard_single_8mib_five_rewrites -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated coverage_goal3 -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated coverage_goal4 --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated coverage_goal4 -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount -- --nocapture --test-threads 1
```

Fresh verification results:

- `cargo fmt`: passed.
- `layerstack lease_aware`: 15 focused tests passed, including the new large
  file and multi-lease storage tests.
- Non-live E2E compile/filter: 1 test passed and skipped live execution without
  `--features e2e`.
- `cargo run -p xtask -- package`: passed, packaged
  `dist/eosd-linux-amd64` with sha256
  `97e3fb1ee24b78c7094c0c8d126475d40d162c6fc040fd3c1d25173699bac571`.
- `live-remount-complex-integrity-1`: 30/30 live `workspace-runtime-isolated`
  tests passed. Runner duration was 45,202 ms, suite duration was 42,485 ms,
  `max_parallel=1`, `container_weight_cap=10`, `daemon_logs_copied=1`, and
  `removed_containers=1`.
- The complex command live-remount trace reported `duration_us=47000`,
  `live_remount=true`, `process_count=2`, `quiesced_process_count=2`,
  `process_resumed=true`, `mount_verified=true`, `lease_retargeted=true`,
  `compacted_snapshot_layers=11`, `remounted_layer_count=1`,
  `before_layer_dirs=11`, `after_layer_dirs=2`,
  `before_storage_bytes=657563`, `after_storage_bytes=262745`,
  `pinned_cwd_count=0`, `pinned_fd_count=0`,
  `pinned_mapped_file_count=0`, and exact lowerdir proof success.
- `live-remount-multi-command-1`: 30/31 live tests passed before the new
  two-command assertion rejected a persisted digest with a trailing newline. The
  trace still proved the remount path succeeded with `live_remount=true`,
  `remountable_commands=2`, `process_count=4`, `quiesced_process_count=4`,
  `mount_verified=true`, `lease_retargeted=true`, `before_layer_dirs=13`,
  `after_layer_dirs=2`, `before_storage_bytes=1182255`, and
  `after_storage_bytes=393845`.
- `live-remount-multi-command-2`: 31/31 live `workspace-runtime-isolated` tests
  passed after writing exact digest bytes with `printf "%s"`. Runner duration
  was 49,167 ms, suite duration was 48,143 ms, prebuild was 645 ms,
  `max_parallel=1`, `container_weight_cap=10`, `daemon_logs_copied=1`, and
  `removed_containers=1`.
- The two-command live-remount trace reported `duration_us=45000`,
  `live_remount=true`, `remountable_commands=2`, `process_count=4`,
  `quiesced_process_count=4`, `process_resumed=true`, `mount_verified=true`,
  `lease_retargeted=true`, `compacted_snapshot_layers=13`,
  `remounted_layer_count=1`, `before_layer_dirs=13`, `after_layer_dirs=2`,
  `before_storage_bytes=1182255`, `after_storage_bytes=393845`,
  `pinned_cwd_count=0`, `pinned_fd_count=0`,
  `pinned_mapped_file_count=0`, `mountinfo_checked_count=4`, and exact
  lowerdir proof success.
- `live-remount-mixed-tree-1`: 33/33 live `workspace-runtime-isolated` tests
  passed. Runner duration was 54,713 ms, suite duration was 53,109 ms, prebuild
  was 1,256 ms, `max_parallel=1`, `container_weight_cap=10`,
  `daemon_logs_copied=1`, and `removed_containers=1`.
- The mixed blocked-command trace reported `duration_us=12000`,
  `reason=fd_pinned_workspace`, `active_commands=2`,
  `remountable_commands=2`, `process_count=4`, `quiesced_process_count=4`,
  `pinned_fd_count=1`, `pinned_cwd_count=0`, `mountinfo_checked_count=4`,
  `resumed=true`, `before_layer_dirs=9`, `after_layer_dirs=9`,
  `fallback_checkpoint_count=0`, and `fallback_compacted_layers=0`.
- The process-tree live-remount trace reported `duration_us=58000`,
  `live_remount=true`, `remountable_commands=1`, `process_count=3`,
  `quiesced_process_count=3`, `process_resumed=true`, `mount_verified=true`,
  `lease_retargeted=true`, `compacted_snapshot_layers=19`,
  `remounted_layer_count=1`, `before_layer_dirs=19`, `after_layer_dirs=2`,
  `before_storage_bytes=4722212`, `after_storage_bytes=1573520`,
  `pinned_cwd_count=0`, `pinned_fd_count=0`,
  `pinned_mapped_file_count=0`, `mountinfo_checked_count=3`, and exact
  lowerdir proof success.
- `live-remount-multi-lease-1`: failed after the new caller-specific stdout
  helper looked at `stdout` while command progress returned
  `output.stdout`. The command had printed `MULTI_LEASE_READY`, but the helper
  timed out before cleanup, leaving two open callers and cascading later setup
  failures.
- `live-remount-multi-lease-2`: 34/34 live `workspace-runtime-isolated` tests
  passed after the helper fix and cleanup-guard change. Runner duration was
  58,928 ms, suite duration was 58,441 ms, prebuild was 128 ms,
  `max_parallel=1`, `container_weight_cap=10`, `daemon_logs_copied=1`, and
  `removed_containers=1`.
- The multi-lease live-remount trace reported `duration_us=45000`,
  `live_remount=true`, `active_leases_after=2`, `remountable_commands=1`,
  `process_count=2`, `quiesced_process_count=2`, `process_resumed=true`,
  `mount_verified=true`, `lease_retargeted=true`,
  `compacted_snapshot_layers=13`, `remounted_layer_count=1`,
  `before_manifest_depth=13`, `after_manifest_depth=3`,
  `before_layer_dirs=13`, `after_layer_dirs=8`,
  `before_storage_bytes=3148141`, `after_storage_bytes=1836071`, and exact
  lowerdir proof success. The test also verified the older lease still read its
  pinned historical snapshot after the newer lease was retargeted, and the
  newer resumed command wrote the expected hash from the compacted snapshot.
- `live-remount-repeat-cycles-1`: 35/35 live `workspace-runtime-isolated` tests
  passed. Runner duration was 59,584 ms, suite duration was 59,119 ms, prebuild
  was 117 ms, `max_parallel=1`, `container_weight_cap=10`,
  `daemon_logs_copied=1`, and `removed_containers=1`.
- The repeated-cycle live test ran three verified remounts in one long-running
  command. Cycle 1 reported `duration_us=43000`, `before_layer_dirs=13`,
  `after_layer_dirs=2`, `before_storage_bytes=1575277`,
  `after_storage_bytes=262705`, `compacted_snapshot_layers=13`, and exact
  lowerdir proof success. Cycles 2 and 3 ran after public head writes that the
  isolated lease must not observe; they reported `duration_us=45000` and
  `duration_us=41000`, `before_layer_dirs=3`, `after_layer_dirs=2`,
  `before_storage_bytes=393932`, and `after_storage_bytes=262705`. The command
  verified the pinned snapshot hash after every resume, and the final private
  state file contained all three cycle hashes.
- `hard-vs-remount-report-1`: 35/35 live `workspace-runtime-isolated` tests
  passed as the current report calibration run. Runner duration was 59,595 ms,
  suite duration was 59,115 ms, prebuild was 118 ms, `max_parallel=1`,
  `container_weight_cap=10`, `daemon_logs_copied=1`, and
  `removed_containers=1`. Live-remount traces used in the performance report
  measured 51 ms for complex command integrity, 47 ms for process-tree/private
  state integrity, 33/43/43 ms for repeated remount cycles, 45 ms for a newer
  lease remounted while an older lease stayed pinned, and 44 ms for two
  remountable commands. Blocked unsafe traces completed in 2-12 ms with
  `fallback_compacted_layers=0`.
- `live-remount-many-file-tree-2`: 36/36 live `workspace-runtime-isolated`
  tests passed after scoped stale-container cleanup. Runner duration was
  64,223 ms, suite duration was 63,735 ms, prebuild was 131 ms,
  `max_parallel=1`, `container_weight_cap=10`, `daemon_logs_copied=1`, and
  `removed_containers=1`. The new many-file trace reported
  `duration_us=49000`, `live_remount=true`, `mount_verified=true`,
  `lease_retargeted=true`, `compacted_snapshot_layers=98`,
  `before_manifest_depth=98`, `after_manifest_depth=1`,
  `before_layer_dirs=98`, `after_layer_dirs=2`,
  `before_storage_bytes=1590724`, `after_storage_bytes=1053681`,
  `remountable_commands=1`, `process_count=2`, `quiesced_process_count=2`, and
  exact lowerdir proof success. The test verifies the resumed command hashes
  the pinned 32-file tree, an isolated read still sees the pre-remount snapshot,
  and a separate public caller sees the post-remount head update.
- `live-remount-three-lease-two-command-1`: 37/37 live
  `workspace-runtime-isolated` tests passed. Runner duration was 69,639 ms,
  suite duration was 69,135 ms, prebuild was 127 ms, `max_parallel=1`,
  `container_weight_cap=10`, `daemon_logs_copied=1`, and
  `removed_containers=1`. The new three-lease/two-command trace reported
  `duration_us=47000`, `live_remount=true`, `mount_verified=true`,
  `lease_retargeted=true`, `compacted_snapshot_layers=25`,
  `before_manifest_depth=25`, `after_manifest_depth=5`,
  `before_layer_dirs=25`, `after_layer_dirs=21`,
  `before_storage_bytes=3150002`, `after_storage_bytes=3147742`,
  `remountable_commands=2`, `process_count=4`, `quiesced_process_count=4`, and
  `active_leases_after=3`. The modest reclaim is expected: the two historical
  leases intentionally pin most lower layers, so this test is primarily a
  correctness and bounded-retarget proof under retained-history pressure.
- `live-remount-process-fanout-1`: 38/38 live `workspace-runtime-isolated`
  tests passed. Runner duration was 73,608 ms, suite duration was 73,113 ms,
  prebuild was 128 ms, `max_parallel=1`, `container_weight_cap=10`,
  `daemon_logs_copied=1`, and `removed_containers=1`. The fanout trace reported
  `duration_us=64000`, `live_remount=true`, `mount_verified=true`,
  `lease_retargeted=true`, `compacted_snapshot_layers=25`,
  `before_manifest_depth=25`, `after_manifest_depth=1`,
  `before_layer_dirs=25`, `after_layer_dirs=2`,
  `before_storage_bytes=4722865`, `after_storage_bytes=786993`,
  `remountable_commands=1`, `process_count=22`, `quiesced_process_count=22`,
  and `active_leases_after=1`. This is the strongest process-count proof so
  far: live remount stopped, inspected, verified, retargeted, and resumed a
  command session with 22 processes while preserving private state and pinned
  public snapshot semantics.
- `live-remount-hard-batch-1`: 41/41 live `workspace-runtime-isolated` tests
  passed. Runner duration was 87,677 ms, suite duration was 87,183 ms, prebuild
  was 131 ms, `max_parallel=1`, `container_weight_cap=10`,
  `daemon_logs_copied=1`, and `removed_containers=1`.
- The large same-file trace reported `duration_us=53000`,
  `live_remount=true`, `mount_verified=true`, `lease_retargeted=true`,
  `compacted_snapshot_layers=10`, `before_manifest_depth=10`,
  `after_manifest_depth=1`, `before_layer_dirs=10`, `after_layer_dirs=2`,
  `before_storage_bytes=9439132`, `after_storage_bytes=2097713`,
  `remountable_commands=1`, `process_count=2`, `quiesced_process_count=2`, and
  `active_leases_after=1`.
- The historical-release trace reported two verified remounts in one
  still-running command. While four leases were active, the first remount
  reported `duration_us=52000`, `before_layer_dirs=17`, `after_layer_dirs=18`,
  `before_storage_bytes=2100185`, `after_storage_bytes=2230163`,
  `active_leases_after=4`, and `process_count=2`. This temporary growth is
  expected because old leases still pin the historical lowerdirs while the
  newest lease gains a compact parent checkpoint. After the three older leases
  released, the second remount reported `duration_us=44000`,
  `before_layer_dirs=9`, `after_layer_dirs=2`,
  `before_storage_bytes=1181102`, `after_storage_bytes=262705`, and
  `active_leases_after=1`, proving the command can remain running while
  released historical layers are reclaimed.
- The three-command wide-tree trace reported `duration_us=44000`,
  `live_remount=true`, `mount_verified=true`, `lease_retargeted=true`,
  `compacted_snapshot_layers=50`, `before_manifest_depth=50`,
  `after_manifest_depth=1`, `before_layer_dirs=50`, `after_layer_dirs=2`,
  `before_storage_bytes=1188793`, `after_storage_bytes=592378`,
  `remountable_commands=3`, `process_count=6`, `quiesced_process_count=6`, and
  `active_leases_after=1`.
- `live-remount-matrix-batch-1`: 47/47 live `workspace-runtime-isolated` tests
  passed. Runner duration was 122,101 ms, suite duration was 121,656 ms,
  prebuild was 132 ms, `max_parallel=1`, `container_weight_cap=10`,
  `daemon_logs_copied=1`, and `removed_containers=1`.
- The six generated matrix traces reported:
  - deep tree, 18 files x 3 rewrites: `duration_us=46000`,
    `before_layer_dirs=56`, `after_layer_dirs=2`,
    `before_storage_bytes=895492`, `after_storage_bytes=593741`,
    `remountable_commands=2`, `process_count=4`.
  - many tiny files, 36 files x 2 rewrites: `duration_us=48000`,
    `before_layer_dirs=74`, `after_layer_dirs=2`,
    `before_storage_bytes=310245`, `after_storage_bytes=302402`,
    `remountable_commands=3`, `process_count=6`.
  - medium-large, 10 files x 5 rewrites: `duration_us=53000`,
    `before_layer_dirs=52`, `after_layer_dirs=2`,
    `before_storage_bytes=3286241`, `after_storage_bytes=1313246`,
    `remountable_commands=4`, `process_count=8`.
  - nested rewrite, 16 files x 4 rewrites: `duration_us=50000`,
    `before_layer_dirs=66`, `after_layer_dirs=2`,
    `before_storage_bytes=2109384`, `after_storage_bytes=1052344`,
    `remountable_commands=4`, `process_count=8`.
  - single hot file, 12 x 512 KiB: `duration_us=49000`,
    `before_layer_dirs=14`, `after_layer_dirs=2`,
    `before_storage_bytes=6294120`, `after_storage_bytes=1049328`,
    `remountable_commands=1`, `process_count=2`.
  - wide sparse tree, 48 files x 1 rewrite: `duration_us=46000`,
    `before_layer_dirs=50`, `after_layer_dirs=2`,
    `before_storage_bytes=405993`, `after_storage_bytes=796250`,
    `remountable_commands=2`, `process_count=4`.
- Base-subtracted interpretation for `live-remount-matrix-batch-1` is recorded
  in `docs/layerstack-hard-protection-vs-remount_PERFORMANCE_REPORT.md`.
  The key policy result is that rewrite-heavy rows improve strongly over `B`
  while the wide sparse row gets worse over `B`, proving that layer depth is not
  a sufficient remount trigger without byte/rewrite-density pressure.
- Expanded matrix non-live retry: 33/33 matching tests passed, then the
  pinned-history max-case exact live retry passed 1/1 in 6.13s after changing
  pinned-history assertions to require mounted-manifest shrink instead of
  immediate global layer-dir shrink while historical leases remain active.
- Concurrent pip-style install tree exact live proof: 1/1 matching test passed
  in 4.56s after reshaping the command into install-Python, Bash wait, and
  verify-Python phases. It creates hundreds of private upperdir files before
  remount, verifies `RECORD` coverage, requires `live_remount=true`,
  `remountable_commands=1`, exact lowerdir proof, zero cwd/fd/mapped pins, then
  validates the pre/post private install-tree SHA-256 after resume.
- Broad compact-remount direct Cargo live filter: 34/34 matching tests passed
  with 29 filtered out in 202.32s. This proves the pip-style case together with
  the expanded single-lease matrix, pinned-history matrix, repeated-cycle,
  multi-command, and historical-lease cases.
- Scoped Docker cleanup during the pip-style retry removed 11 stale containers
  by exact `eos.e2e.run_id` using
  `/tmp/ephemeral-os-remount-target/debug/e2e-reap --run-id ...`; no active
  local Cargo/E2E owner process was present before reaping.
- `coverage_goal2` non-live compile/filter: 16/16 matching tests passed with
  63 filtered out.
- `coverage_goal2` live proof: 16/16 matching tests passed with 63 filtered out
  in 143.09s. The batch adds 5 easy, 5 medium, and 6 hard classified sets,
  including four pinned-history cases and single-lease rows up to 8 MiB files,
  192 sparse files, eight command groups, and four historical leases plus one
  live lease.
- Broad compact-remount non-live inventory after `coverage_goal2`: 50/50
  matching tests passed with 29 filtered out. At that stage, the direct
  compact-remount inventory was 50 sets and the later fallback-removal decision
  still needed broader evidence.
- `coverage_goal3` non-live compile/filter: 20/20 matching tests passed with
  79 filtered out.
- Initial `coverage_goal3` live proof: 19/20 matching tests passed in 177.11s.
  The one failed row tried a 16 MiB single-write file and then a 12 MiB
  single-write file; both exceeded live write limits before remount ran. The
  failure established the current API limit for one `sandbox.file.write`
  payload: 8 MiB.
- Corrected 8 MiB hard-row exact live retry: 1/1 matching test passed with 98
  filtered out in 7.82s.
- `coverage_goal3` live proof after the 8 MiB fix: 20/20 matching tests passed
  with 79 filtered out in 184.79s.
- Broad compact-remount non-live inventory after `coverage_goal3`: 70/70
  matching tests passed with 29 filtered out. At that stage, the direct
  compact-remount inventory was 70 sets and the later fallback-removal decision
  still needed the final coverage batch.
- Real concurrent pip install hard-row non-live compile/filter: 1/1 matching
  test passed with 99 filtered out. This row generates two local Python
  packages and runs two `python3 -m pip install --no-index --target ...`
  processes concurrently, avoiding network dependency while exercising a real
  pip install tree rather than the earlier synthetic pip-style writer.
- Real concurrent pip install hard-row exact live proof: 1/1 matching test
  passed with 99 filtered out in 7.10s. The test verified at least 500
  installed files in the private upperdir, `live_remount=true`,
  `mount_verified=true`, `lease_retargeted=true`, exact lowerdir proof, zero
  cwd/fd/mapped pins, post-remount tree SHA-256 stability, package imports,
  sample module/data files, and pinned public snapshot isolation after a public
  head update.
- Broad compact-remount non-live inventory after the real pip row: 71/71
  matching tests passed with 29 filtered out. At that stage, the direct
  compact-remount inventory was 71 sets and the final `coverage_goal4` batch
  was still needed.
- `coverage_goal4` non-live compile/filter: 30/30 matching tests passed with
  99 filtered out. The final batch contains 12 easy, 10 medium, and 8 hard
  rows when counting the already-added real concurrent pip row as the first
  hard `coverage_goal4` case.
- `coverage_goal4` live proof: 30/30 matching tests passed with 99 filtered out
  in 262.59s. The live batch includes the real concurrent pip install row,
  large same-file rewrites up to 4 MiB x 8, eight-command sparse/wide rows,
  and pinned-history rows with up to four historical readers.
- Broad compact-remount non-live inventory after `coverage_goal4`: 100/100
  matching tests passed with 29 filtered out. The requested inventory count is
  now met. The distribution accounting is 40 easy, 30 medium, and 30 hard:
  the existing baseline through `coverage_goal3` classifies as 28 easy,
  20 medium, and 22 hard, and `coverage_goal4` adds 12 easy, 10 medium, and
  8 hard.
- Broad compact-remount live proof after `coverage_goal4`: 100/100 matching
  tests passed with 29 filtered out in 802.03s. This is the first full direct
  live proof over the 100-case compact-remount inventory. At that point the
  hard-protection fallback-removal product-code decision remained open; the
  later closeout section records the implemented report-only replacement.

Stress closeout verification:

```bash
cargo fmt --check
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -q -p layerstack --release --example bench_layerstack_gap_reclaim
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p layerstack
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p operation --all-targets
jq -e '.passed == true and .max_parallel == 1 and .container_weight_cap == 10 and ([.suites[].status] | all(. == 0))' crates/e2e-test/test-reports/runs/live-remount-mixed-tree-1/summary.json
git diff --check
```

Stress closeout results:

- `cargo fmt --check`: passed.
- `bench_layerstack_gap_reclaim`: passed and refreshed the table above.
- `layerstack`: 102 unit tests, 1 CAS fixture test, 19 stack integration tests,
  and doc tests passed.
- `cargo run -p xtask -- package`: passed, packaged
  `dist/eosd-linux-amd64` with sha256
  `97e3fb1ee24b78c7094c0c8d126475d40d162c6fc040fd3c1d25173699bac571`.
- `operation --all-targets`: 81 unit tests, 4 checkpoint tests, and 1 contract
  test passed.
- Live summary gate for `live-remount-mixed-tree-1`: passed.
- `git diff --check`: passed.

## Blocked Fallback Removal and Real Pip Regression Proof

### Code Delta

- `crates/daemon/core/src/runtime/workspace.rs`
  - `BoundState::blocked_remount_report_for_test` no longer calls
    `LayerStack::reclaim_lease_aware_checkpoints`.
  - `WorkspaceRemountBlockedReport` now carries
    `fallback_compaction_enabled=false` and
    `fallback_compaction_policy="disabled_report_only"`.
  - Blocked reports set fallback checkpoint, compacted-layer, and skipped-gap
    counters to zero, then re-read metrics without mutating LayerStack state.
- `crates/daemon/core/src/op_adapter/isolation.rs`
  - `op_test_compact_remount` emits the disabled-fallback fields in both the
    `lease_remount_blocked` trace event and refused response.
  - The refused message is now
    `live remount blocked; no fallback compaction attempted`.
  - The trace event is bounded/count-based for verbose diagnostics:
    `command_id_count`, `process_group_count`, and
    `inspection_detail_present`; the refused response retains full
    `command_ids`, `process_group_ids`, and `inspection_detail`.
- `crates/daemon/core/tests/unit/workspace_runtime.rs`
  - Removed a stale legacy constructor call by switching the fixture to
    `CommandOps::with_commit_options_and_capture_options` with
    `BoundedCaptureOptions::default()`.
- `crates/e2e-test/tests/workspace-runtime-isolated/isolated_workspace_compact_remount.rs`
  - Blocked-remount tests now assert pressure-only semantics:
    disabled fallback policy, zero fallback counters, unchanged
    before/after LayerStack metrics, and compact non-truncated trace fields.
  - The hard `coverage_goal4` real pip row remains the required
    hundreds-of-files workload: two local packages, two concurrent
    `python3 -m pip install --no-index --target ...` processes, at least
    500 installed files, post-remount tree hash, imports, module/data reads,
    and public snapshot isolation.

### Verification

```bash
cargo fmt --check
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p daemon compact_remount --lib
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p xtask -- package
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_blocks -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree -- --nocapture --test-threads 1
```

Results:

- `cargo fmt --check`: passed after formatting the daemon fixture.
- `daemon compact_remount --lib`: passed compile gate; 0 matched tests,
  69 filtered out.
- Broad compact-remount no-feature E2E gate: passed with 108 matching tests and
  21 filtered out. This covers blocked cases plus the full live-remount
  inventory in non-live skip mode.
- `xtask package`: passed using `package-fast`; packaged
  `dist/eosd-linux-amd64` with sha256
  `5561d438aa1f2c6fa01feb9c3783fac0fc3ba722c994031cdc7fef43bef68e6d`.
- Focused live blocked-remount proof after compact trace fields: passed with
  6/6 matching tests and 123 filtered out in 14.48s.
- Exact live real concurrent pip proof: passed with 1/1 matching test and
  128 filtered out in 6.93s.
- Real concurrent pip space/time bench rerun: passed with 1/1 matching test
  and 128 filtered out in 6.96s after a 2.21s test-binary compile. It installed
  786 files, reached install-ready in 2,759 ms, completed the verified live
  remount operation in 75 ms, completed post-remount verification in 385 ms,
  reduced LayerStack storage from 1,772,814 bytes to 197,168 bytes
  (1,575,646 bytes saved, 88.88% reduction), reduced layer dirs from 19 to 2,
  and reduced manifest depth from 19 to 1.
- Final broad live compact-remount proof after fallback removal: passed with
  100/100 matching tests and 29 filtered out in 789.36s. This is the current
  package proof over the full 40 easy / 30 medium / 30 hard inventory,
  including real concurrent pip install, large-file rewrites, many-file rows,
  high-command rows, historical-reader rows, repeated remount cycles, and
  complex command-integrity rows.
- Scoped cleanup removed the failed blocked-run container
  `run-14024-18b9b4a871889a28-1` and the successful blocked-run container
  `run-16549-18b9b4c004b030b0-1`.
- `CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p layerstack`:
  passed with 102 unit tests, 1 CAS fixture test, 19 stack integration tests,
  and doc tests.
- `CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p operation --all-targets`:
  passed with 81 unit tests, 4 checkpoint tests, and 1 contract test.
- Final `cargo fmt --check`: passed.
- Final `git diff --check`: passed.
- Scoped cleanup removed the exact real-pip live proof container
  `run-17552-18b9b4ca2b8c6af0-1`.
- Scoped cleanup removed the final broad live proof containers
  `run-26705-18b9b51ce97aa6d8-1`.
- Scoped cleanup removed the real concurrent pip space/time bench container
  `run-60668-18b9cb1598ccd7d8-1`.

### Verdict

Proceed with the report-only blocked path. Verified live remount remains the
only path that may retarget a running lease and reclaim mounted parent-prefix
layers. Blocked sessions now emit pressure telemetry and leave cleanup to lease
release or a separate explicit maintenance operation.
