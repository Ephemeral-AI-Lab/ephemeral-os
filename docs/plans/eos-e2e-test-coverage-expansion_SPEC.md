# SPEC: `eos-e2e-test` Coverage Expansion and Module README Contracts

Status: DRAFT
Date: 2026-06-05
Owner doc: `docs/plans/eos-e2e-test-coverage-expansion_SPEC.md`
Scope: `sandbox/crates/eos-e2e-test/tests`, `sandbox/crates/eos-e2e-test/src`,
and module-local E2E config files under `sandbox/crates/eos-e2e-test/tests/*/config`.

This spec converts the read-only coverage plan into implementation requirements
for expanding the Rust sandbox E2E suite. It does not implement tests. The first
implementation step is documentation: create one module `readme.md` per test
target, then add correctness, performance, resource, and leak coverage against
the contracts below.

The E2E harness remains protocol-only: operations under test go through
`eos-protocol` against a live `eosd`. Docker lifecycle is harness
infrastructure, not a sandbox operation oracle.

---

## 1. Goals

1. Add a `readme.md` coverage contract for every integration target under
   `sandbox/crates/eos-e2e-test/tests`.
2. Make each README load-bearing: every checklist item must be covered by at
   least one listed test case.
3. Expand correctness coverage for OCC, ephemeral workspaces, isolated
   workspaces, file ops, command sessions, plugins, LayerStack/overlay, daemon
   control, and pressure.
4. Promote performance and resource behavior to first-class E2E assertions.
5. Add explicit concurrency comparisons at levels `1`, `3`, `6`, and `12`
   where the subsystem can support those levels.
6. Keep fast developer runs separate from heavy/performance runs through
   module-local YAML and typed harness config, not ad hoc environment
   overrides.

## 2. Non-Goals

- No implementation in this spec.
- No model-facing tool rename.
- No daemon wire op rename.
- No non-Docker sandbox provider.
- No agent-core workflow or LLM coverage in `eos-e2e-test`.
- No reliance on stale Python backend surfaces.
- No test oracle that bypasses the daemon protocol for sandbox behavior. Docker
  and container process scans are allowed only for harness lifecycle and leak
  probes where the daemon protocol has no equivalent signal.

---

## 3. Current Harness Facts

The crate already has 8 integration targets and roughly 114 live test
functions:

| Target | Current source files |
|---|---|
| `core` | `command_sessions.rs`, `direct_file_ops.rs`, `envelope_contract.rs`, `errors_and_limits.rs`, `runtime_setup.rs`, `smoke_paths.rs` |
| `daemon` | `control_cancel.rs`, `control_heartbeat.rs`, `control_inflight.rs`, `op_registration.rs`, `runtime_identity.rs` |
| `ephemeral_workspace` | `command_sessions.rs`, `overlay_exec.rs` |
| `isolated_workspace` | `command_sessions.rs`, `lifecycle.rs`, `network.rs`, `no_publish.rs`, `tool_routing.rs` |
| `layerstack` | `commit_to_git.rs`, `commit_to_workspace.rs`, `lease.rs`, `squash.rs`, `squash_bounds.rs`, `squash_deep.rs` |
| `occ` | `gating.rs`, `merge.rs` |
| `plugin` | `isolated_gate.rs`, `lsp.rs`, `packages.rs` |
| `pressure` | `concurrency.rs`, `cross_subsystem.rs`, `failure_recovery.rs` |

Existing harness commands:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo test -p eos-e2e-test
cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist
cargo test -p eos-e2e-test --features e2e -- --nocapture
cargo test -p eos-e2e-test --features e2e --test <target> <filter> -- --nocapture
```

Live defaults come from `sandbox/config/prd.yml`; each target may merge one
module-local `config/default.test.yml`. Today those overrides are thin:

- Most targets enable `isolated_workspace` with smaller upperdir and memory
  settings.
- `layerstack` sets `daemon.layer_stack.auto_squash_max_depth: 8`.
- `plugin` forces `eos_e2e_test.pool.mode: per-test` and disables kept
  containers.

The harness config structs use strict typed fields. New workload knobs must be
added to the typed config before YAML can carry them.

---

## 4. README Contract

Create these files before expanding tests:

```text
sandbox/crates/eos-e2e-test/tests/core/readme.md
sandbox/crates/eos-e2e-test/tests/daemon/readme.md
sandbox/crates/eos-e2e-test/tests/ephemeral_workspace/readme.md
sandbox/crates/eos-e2e-test/tests/isolated_workspace/readme.md
sandbox/crates/eos-e2e-test/tests/layerstack/readme.md
sandbox/crates/eos-e2e-test/tests/occ/readme.md
sandbox/crates/eos-e2e-test/tests/plugin/readme.md
sandbox/crates/eos-e2e-test/tests/pressure/readme.md
```

Each README must use exactly this section structure:

```md
# <module>

## Overview

## Checklist

- [ ] <module>-<stable-id>: <target behavior>

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
```

README requirements:

1. The overview names the owning subsystem, the daemon ops under test, the
   module-local config path, and whether the target is fast, heavy, perf, or
   mixed.
2. Checklist IDs are stable and local to the module, for example
   `occ-git-drop` or `iws-port-matrix`.
3. Every checklist item appears in at least one `Test Case` row.
4. Every test function in the module appears in the table, or the README says
   why it is only support/legacy coverage.
5. Commands use Cargo filters that a developer can run directly from
   `sandbox/`.
6. README text records known heavy tests but does not record transient pass/fail
   claims.

Add a later validation check that compares `cargo test -p eos-e2e-test
--features e2e -- --list` or an `rg` inventory against the README tables.

---

## 5. Config and Profile Contract

Keep `config/default.test.yml` as the default target config. Add profile files
only when tests need distinct cost classes:

```text
tests/<module>/config/fast.test.yml
tests/<module>/config/heavy.test.yml
tests/<module>/config/perf.test.yml
```

Required typed workload config additions:

| Field | Purpose |
|---|---|
| `workload.concurrency_levels` | Default `[1, 3, 6, 12]` for ladder tests. |
| `workload.write_iterations` | Bound repeated write/squash/refresh loops. |
| `workload.sample_count` | Perf sample count before summary artifact emission. |
| `workload.heavy_enabled` | Allows expensive tests to skip unless selected by profile. |
| `workload.perf_artifact_dir` | Directory for JSON performance reports. |
| `workload.timeout_s` | Heavy/perf operation budget independent of socket timeout. |

Fast profile:

- Small loops.
- Single daemon or default pool.
- No long-running 12-way isolated handle pressure unless the configured cap
  supports it.

Heavy profile:

- Full `1/3/6/12` ladders where valid.
- Larger audit pull limits.
- Short TTL/reaper settings where the test owns the daemon config.
- JSON performance/resource artifact output.

Perf profile:

- Repeated samples.
- No fragile absolute latency assertions in the first version.
- Structural bounds first: O(1) resource shape, no monotonic leaks, no orphaned
  sessions or processes.

---

## 6. Global Performance and Resource Oracles

Use at least two independent signals for resource-sensitive tests.

Protocol-visible oracles:

- `api.layer_metrics`: `manifest_depth`, `active_leases`, `leased_layers`,
  `layer_dirs`, `referenced_layers`, `staging_dirs`, `storage_bytes`.
- `api.v1.command_session_count`.
- `api.plugin.status`.
- `api.audit.pull`.
- Response `timings`, including `runtime.dispatch_s`,
  `api.write.occ_apply_s`, `api.read.layer_stack_read_s`,
  `resource.command_exec.upperdir_tree_bytes`,
  `resource.command_exec.run_dir_tree_bytes`,
  `resource.command_exec.workspace_tree_bytes`,
  `resource.cgroup.cpu_*`, and `resource.cgroup.io_*`.

Audit-visible oracles:

- `occ.publish`.
- `occ.conflict`.
- `layer_stack.lease_acquired`.
- `layer_stack.lease_released`.
- `layer_stack.squash_triggered`.
- `layer_stack.squash_completed`.
- `overlay_workspace.cleanup`.

Host/container probes allowed only for leak checks:

- `/proc` marker process scans after cancel, terminate, reload, and daemon
  cleanup.
- File/socket cleanup checks under `/eos/scratch` only when daemon protocol does
  not expose an equivalent signal.

Do not rely on zero-valued `orphan_layer_count` or `missing_layer_count` as the
only leak oracle. Treat them as supplemental until they are backed by real
enumeration.

---

## 7. Module Coverage Contracts

### 7.1 `occ`

Checklist:

| ID | Requirement |
|---|---|
| `occ-git-drop` | `.git/**` changes are dropped, unreadable, and do not advance manifest state. |
| `occ-gitignored-direct` | Gitignored paths route direct and bypass gated OCC hash checks. |
| `occ-tracked-gated` | Non-gitignored paths route gated and publish through OCC. |
| `occ-disjoint-merge` | Concurrent disjoint tracked writes all commit and remain readable. |
| `occ-conflict-report` | Concurrent same-path edits produce structured conflict results and coherent final content. |
| `occ-edit-anchor-errors` | Missing anchor and ambiguous multiple occurrence edits return no-op conflict payloads. |
| `occ-audit-accounting` | Publish and conflict paths emit audit events and route timing counters. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `git_writes_are_dropped_and_unreadable` | Strengthen existing test with unchanged `manifest_version`, `manifest_depth`, empty `changed_paths`, route counts zero, and readback absent. | `cargo test -p eos-e2e-test --features e2e --test occ git_writes_are_dropped_and_unreadable -- --nocapture` | `occ-git-drop` |
| `gitignored_writes_bypass_the_occ_gate` | Keep existing direct route check and tracked sibling control. | `cargo test -p eos-e2e-test --features e2e --test occ gitignored_writes_bypass_the_occ_gate -- --nocapture` | `occ-gitignored-direct`, `occ-tracked-gated` |
| `concurrent_gitignored_same_path_direct_writes` | New: two ignored writes race on the same path; both avoid stale-base conflict and final content is one whole payload. | `cargo test -p eos-e2e-test --features e2e --test occ concurrent_gitignored_same_path_direct_writes -- --nocapture` | `occ-gitignored-direct` |
| `concurrent_disjoint_writes` | Keep existing correctness oracle for concurrent disjoint tracked paths. Do not assert queue batching until a non-atomic path exists. | `cargo test -p eos-e2e-test --features e2e --test occ concurrent_disjoint_writes -- --nocapture` | `occ-disjoint-merge` |
| `concurrent_conflicting_writes` | Strengthen to exact one committed writer and `N - 1` conflict or structured rejection results. | `cargo test -p eos-e2e-test --features e2e --test occ concurrent_conflicting_writes -- --nocapture` | `occ-conflict-report` |
| `edit_anchor_error_no_publish` | New or strengthened: missing anchor/multiple occurrence preserves content, `applied_edits == 0`, `changed_paths == []`, and manifest unchanged. | `cargo test -p eos-e2e-test --features e2e --test occ edit_anchor_error_no_publish -- --nocapture` | `occ-edit-anchor-errors`, `occ-audit-accounting` |

### 7.2 `ephemeral_workspace`

Checklist:

| ID | Requirement |
|---|---|
| `eph-per-call-workspace` | Each shell/exec operation gets a fresh ephemeral overlay over the latest LayerStack manifest. |
| `eph-outside-direct-fs` | Writes outside the workspace are not OCC-captured and land directly in the container filesystem. |
| `eph-upperdir-delta` | Upperdir bytes scale with changed bytes, not lowerdir/repo size. |
| `eph-overlay-cleanup` | Completed exec releases layer leases and removes overlay scratch. |
| `eph-occ-publish` | In-workspace exec changes publish through OCC after tool finish. |
| `eph-stale-exec-conflict` | Long-running exec from stale snapshot cannot overwrite newer direct file content silently. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `exec_write_outside_workspace_is_not_captured` | Existing test for workspace-filtered capture and direct `/tmp` write behavior. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace exec_write_outside_workspace_is_not_captured -- --nocapture` | `eph-outside-direct-fs` |
| `foreground_exec_recycles_overlay_scratch` | Existing test for cleanup audit and lease release. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace foreground_exec_recycles_overlay_scratch -- --nocapture` | `eph-overlay-cleanup` |
| `exec_upperdir_captures_only_the_delta` | Strengthen existing O(1) delta test with larger base and repeated overlay writes. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace exec_upperdir_captures_only_the_delta -- --nocapture` | `eph-upperdir-delta` |
| `exec_overlay_mount_publishes_changed_paths` | Existing in-workspace publish path. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace exec_overlay_mount_publishes_changed_paths -- --nocapture` | `eph-occ-publish`, `eph-per-call-workspace` |
| `long_running_exec_conflicts_after_direct_write` | New: start exec, mutate same file by direct write, release exec, assert conflict or rejection and direct-write content remains. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace long_running_exec_conflicts_after_direct_write -- --nocapture` | `eph-stale-exec-conflict` |

### 7.3 `isolated_workspace`

Checklist:

| ID | Requirement |
|---|---|
| `iws-lifecycle-pin` | Enter pins a LayerStack manifest and exit releases the lease. |
| `iws-private-persistence` | Writes persist across tool calls while the isolated handle is open. |
| `iws-no-publish` | Isolated file and exec writes never publish through OCC. |
| `iws-discard-exit` | Exit discards private upperdir and public workspace cannot read private writes. |
| `iws-network-isolation` | Network namespace isolation allows same-port servers across isolated namespaces. |
| `iws-same-netns-conflict` | Same namespace still conflicts on same port. |
| `iws-exit-cleanup` | Exit tears down scratch, namespace, cgroup, holder, and lease state. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `enter_status_exit_pin_and_teardown` | Existing lifecycle test, strengthened with full exit inspection fields. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace enter_status_exit_pin_and_teardown -- --nocapture` | `iws-lifecycle-pin`, `iws-exit-cleanup` |
| `isolated_write_is_discarded_on_exit` | Existing private write discard test. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace isolated_write_is_discarded_on_exit -- --nocapture` | `iws-private-persistence`, `iws-discard-exit` |
| `isolated_write_does_not_publish_or_release_lease` | Existing no-publish file write test. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace isolated_write_does_not_publish_or_release_lease -- --nocapture` | `iws-no-publish` |
| `isolated_exec_write_is_private_and_discarded` | New: exec writes inside isolated mode, read succeeds while open, exit discards, no `occ.publish`. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace isolated_exec_write_is_private_and_discarded -- --nocapture` | `iws-no-publish`, `iws-discard-exit` |
| `cross_mode_same_port_no_conflict` | Existing ephemeral plus isolated same-port case. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace cross_mode_same_port_no_conflict -- --nocapture` | `iws-network-isolation` |
| `same_mode_same_port_conflicts` | Existing ephemeral plus ephemeral same-port conflict. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace same_mode_same_port_conflicts -- --nocapture` | `iws-same-netns-conflict` |
| `isolated_to_isolated_same_port_matrix` | New: different caller IDs can bind same port, same caller conflicts, exit/re-enter can reuse port. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace isolated_to_isolated_same_port_matrix -- --nocapture` | `iws-network-isolation`, `iws-same-netns-conflict`, `iws-exit-cleanup` |

### 7.4 `core` File Ops and Command Sessions

Checklist:

| ID | Requirement |
|---|---|
| `core-fast-file-ops` | Direct read/write/edit use fast paths and bypass overlay leasing. |
| `core-file-error-catalog` | Read/write/edit guards and edit conflicts return deterministic payloads. |
| `core-command-lifecycle` | `exec_command`, `write_stdin`, collect, cancel, timeout, and output cap behave correctly. |
| `core-command-cursors` | Session output cursors do not replay consumed output. |
| `core-command-terminate-kills-group` | `write_stdin(terminate)` kills the same-process-group child set. |
| `core-detached-child-contract` | `nohup` and `setsid nohup` behavior is explicitly decided and tested. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `fast_path_write_edit_emit_no_overlay_or_lease_audit` | Existing fast-path audit test. | `cargo test -p eos-e2e-test --features e2e --test core fast_path_write_edit_emit_no_overlay_or_lease_audit -- --nocapture` | `core-fast-file-ops` |
| `direct_file_ops_concurrency_ladder` | New: direct file operations at `1/3/6/12`, no overlay lease/audit, OCC publish emitted, reads match. | `cargo test -p eos-e2e-test --features e2e --test core direct_file_ops_concurrency_ladder -- --nocapture` | `core-fast-file-ops` |
| `edit_error_catalog_anchor_not_found_and_count_mismatch` | Existing error catalog anchor. | `cargo test -p eos-e2e-test --features e2e --test core edit_error_catalog_anchor_not_found_and_count_mismatch -- --nocapture` | `core-file-error-catalog` |
| `write_stdin_echo` | Existing stdin echo behavior. | `cargo test -p eos-e2e-test --features e2e --test core write_stdin_echo -- --nocapture` | `core-command-lifecycle` |
| `command_session_output_cursor_no_replay` | New: poll/write twice and assert output cursor advances without replay. | `cargo test -p eos-e2e-test --features e2e --test core command_session_output_cursor_no_replay -- --nocapture` | `core-command-cursors` |
| `write_stdin_terminate_reaps_marker_process` | New or strengthened: terminate returns session count zero and `/proc` marker count zero. | `cargo test -p eos-e2e-test --features e2e --test core write_stdin_terminate_reaps_marker_process -- --nocapture` | `core-command-terminate-kills-group` |
| `nohup_child_keeps_session_running` | New: `nohup sleep 3 & echo done` remains running until child exits. | `cargo test -p eos-e2e-test --features e2e --test core nohup_child_keeps_session_running -- --nocapture` | `core-detached-child-contract` |
| `setsid_nohup_contract` | New: encode the chosen contract for new-session descendants: block, detect, reap, or explicitly accept. | `cargo test -p eos-e2e-test --features e2e --test core setsid_nohup_contract -- --nocapture` | `core-detached-child-contract` |

### 7.5 `layerstack`

Checklist:

| ID | Requirement |
|---|---|
| `layer-base` | Workspace base creation and rebuild are idempotent and visible through metrics. |
| `layer-lease-pin` | Active leases pin their frozen manifest and release to zero. |
| `layer-squash-depth` | Auto-squash keeps depth bounded by configured target. |
| `layer-squash-gap-formula` | Post-squash depth equals lease heads plus foldable gap runs. |
| `layer-storage-bounded` | Repeated overwrites do not grow durable layer storage linearly. |
| `layer-commit-workspace` | Commit to workspace materializes merged view and emits coherent version/timing data. |
| `layer-commit-git` | Commit to Git works after repeated squash and reports bounded depth. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `enter_acquires_lease` / `exit_releases_lease` | Existing lease lifecycle coverage. | `cargo test -p eos-e2e-test --features e2e --test layerstack lease -- --nocapture` | `layer-lease-pin` |
| `lease_pins_layers_vs_squash` | Existing pinned lease under squash pressure, strengthened with layer-dir retention and release cleanup. | `cargo test -p eos-e2e-test --features e2e --test layerstack lease_pins_layers_vs_squash -- --nocapture` | `layer-lease-pin`, `layer-squash-depth` |
| `squash_keeps_each_lease_head_and_folds_every_gap_live` | New live E2E formula test with multiple active leases and manifest inspection. | `cargo test -p eos-e2e-test --features e2e --test layerstack squash_keeps_each_lease_head_and_folds_every_gap_live -- --nocapture` | `layer-squash-gap-formula` |
| `repeated_overwrite_keeps_storage_bounded` | Existing storage bound coverage. | `cargo test -p eos-e2e-test --features e2e --test layerstack repeated_overwrite_keeps_storage_bounded -- --nocapture` | `layer-storage-bounded` |
| `commit_materializes_merged_view` | Existing commit-to-workspace correctness. | `cargo test -p eos-e2e-test --features e2e --test layerstack commit_materializes_merged_view -- --nocapture` | `layer-commit-workspace` |
| `commit_to_git_commits_overlay_snapshot_after_repeated_squash` | Existing Git commit after squash test. | `cargo test -p eos-e2e-test --features e2e --test layerstack commit_to_git_commits_overlay_snapshot_after_repeated_squash -- --nocapture` | `layer-commit-git` |

### 7.6 `plugin`

Checklist:

| ID | Requirement |
|---|---|
| `plugin-package-ensure` | Warm/cold ensure publishes package and setup roots by digest. |
| `plugin-setup-idempotent` | Re-ensure skips setup when package and setup digests match. |
| `plugin-service-hosted` | Daemon-hosted plugin services run as real processes and are visible in status. |
| `plugin-service-cleanup` | Reload/stop removes routes, PPC clients, service snapshots, sockets, uploads, and marker processes. |
| `plugin-refresh-remount` | Read-only service sees latest workspace after LayerStack update. |
| `plugin-refresh-singleflight` | Concurrent refreshes see new content and keep refresh counts bounded. |
| `plugin-restart-policy` | Restart strategy restarts process instead of remounting. |
| `plugin-isolated-gate` | Plugin operations are rejected while the caller is in isolated mode. |
| `plugin-write-allowed` | Live write-allowed/oneshot overlay plugin paths publish only through daemon-owned OCC paths. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `generic_package_installs_and_sets_up` | Existing warm/cold package install and setup root check. | `cargo test -p eos-e2e-test --features e2e --test plugin generic_package_installs_and_sets_up -- --nocapture` | `plugin-package-ensure` |
| `generic_package_reensure_is_idempotent` | Existing idempotent setup count check. | `cargo test -p eos-e2e-test --features e2e --test plugin generic_package_reensure_is_idempotent -- --nocapture` | `plugin-setup-idempotent` |
| `service_health_probe_reports_connected_service` | Existing live service probe. | `cargo test -p eos-e2e-test --features e2e --test plugin service_health_probe_reports_connected_service -- --nocapture` | `plugin-service-hosted` |
| `package_reload_reaps_old_service_and_routes` | New: load digest A, reload digest B, assert old service marker/process/PPC route/upload state is gone. | `cargo test -p eos-e2e-test --features e2e --test plugin package_reload_reaps_old_service_and_routes -- --nocapture` | `plugin-service-cleanup` |
| `generic_plugin_refreshes_after_workspace_edit` | Existing refresh behavior. | `cargo test -p eos-e2e-test --features e2e --test plugin generic_plugin_refreshes_after_workspace_edit -- --nocapture` | `plugin-refresh-remount` |
| `concurrent_plugin_refresh_singleflight` | New: one workspace edit, `N` concurrent queries, all see new content, refresh count bounded. | `cargo test -p eos-e2e-test --features e2e --test plugin concurrent_plugin_refresh_singleflight -- --nocapture` | `plugin-refresh-singleflight` |
| `restart_service_strategy_restarts_on_workspace_edit` | Existing restart policy test. | `cargo test -p eos-e2e-test --features e2e --test plugin restart_service_strategy_restarts_on_workspace_edit -- --nocapture` | `plugin-restart-policy` |
| `generic_plugin_rejected_in_isolated_workspace` | Existing isolated gate test. | `cargo test -p eos-e2e-test --features e2e --test plugin generic_plugin_rejected_in_isolated_workspace -- --nocapture` | `plugin-isolated-gate` |
| `oneshot_overlay_plugin_write_publishes_through_occ` | New: live write-allowed plugin operation publishes through OCC and reports changed paths. | `cargo test -p eos-e2e-test --features e2e --test plugin oneshot_overlay_plugin_write_publishes_through_occ -- --nocapture` | `plugin-write-allowed` |

### 7.7 `daemon`

Checklist:

| ID | Requirement |
|---|---|
| `daemon-ready-identity` | Runtime readiness exposes daemon identity, probes, and timings. |
| `daemon-op-registry` | Built-in daemon ops are registered and reject unknown ops cleanly. |
| `daemon-inflight` | Background invocations are counted, heartbeated, and cancellable. |
| `daemon-command-control` | Command-session control ops remain coherent under live sessions. |
| `daemon-audit` | Audit pull, pagination, floor behavior, and reset/test hooks are explicitly tested. |
| `daemon-ttl-reaper` | Short TTL/reaper config cleans stale inflight state in heavy profile. |
| `daemon-plugin-control` | Background plugin/PPC operations participate in inflight/cancel/heartbeat control where supported. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `runtime_ready_exposes_daemon_identity` | Existing identity probe. | `cargo test -p eos-e2e-test --features e2e --test daemon runtime_ready_exposes_daemon_identity -- --nocapture` | `daemon-ready-identity` |
| `every_builtin_op_is_wire_routed` | Existing op registry coverage. | `cargo test -p eos-e2e-test --features e2e --test daemon every_builtin_op_is_wire_routed -- --nocapture` | `daemon-op-registry` |
| `inflight_count_observes_concurrent_background_invocations` | Existing background exec inflight count. | `cargo test -p eos-e2e-test --features e2e --test daemon inflight_count_observes_concurrent_background_invocations -- --nocapture` | `daemon-inflight` |
| `live_cancel_of_inflight_sets_cancelled` | Existing cancel inflight coverage, strengthened with cleanup fields where available. | `cargo test -p eos-e2e-test --features e2e --test daemon live_cancel_of_inflight_sets_cancelled -- --nocapture` | `daemon-inflight`, `daemon-command-control` |
| `audit_pull_paginates_and_baselines` | New: audit cursor, pagination, and no reliance on reset floor. | `cargo test -p eos-e2e-test --features e2e --test daemon audit_pull_paginates_and_baselines -- --nocapture` | `daemon-audit` |
| `isolated_workspace_test_reset_behavior` | New: behavior test for `api.isolated_workspace.test_reset` where test gate allows it. | `cargo test -p eos-e2e-test --features e2e --test daemon isolated_workspace_test_reset_behavior -- --nocapture` | `daemon-audit` |
| `inflight_ttl_reaper_heavy` | New heavy-profile TTL reaper test with short `ttl_s` and `reaper_interval_s`. | `cargo test -p eos-e2e-test --features e2e --test daemon inflight_ttl_reaper_heavy -- --nocapture` | `daemon-ttl-reaper` |
| `background_plugin_operation_control` | New: background plugin/PPC op with explicit invocation ID participates in inflight/heartbeat/cancel or documents unsupported behavior. | `cargo test -p eos-e2e-test --features e2e --test daemon background_plugin_operation_control -- --nocapture` | `daemon-plugin-control` |

### 7.8 `pressure`

Checklist:

| ID | Requirement |
|---|---|
| `pressure-ladder-file` | Direct file ops pass at concurrency `1/3/6/12`. |
| `pressure-ladder-exec` | Ephemeral exec passes at concurrency `1/3/6/12` and releases leases. |
| `pressure-ladder-command` | Command sessions start/cancel at `1/3/6/12` and drain session/lease counts. |
| `pressure-ladder-occ` | OCC disjoint writes and same-path conflict pressure return coherent payloads at `1/3/6/12`. |
| `pressure-ladder-plugin` | Plugin refresh/dispatch pressure remains coherent at configured levels. |
| `pressure-isolated-cap` | Isolated handle pressure either runs under a high-cap config or asserts cap rejection for levels beyond default. |
| `pressure-resource-report` | Heavy/perf runs emit JSON summaries for latency shape, resource counters, and leak counters. |

Planned test cases:

| Test name | Description | Command | Checklist |
|---|---|---|---|
| `n_concurrent_mixed_ops` | Existing mixed pressure smoke. | `cargo test -p eos-e2e-test --features e2e --test pressure n_concurrent_mixed_ops -- --nocapture` | `pressure-ladder-file`, `pressure-ladder-exec` |
| `file_ops_ladder_1_3_6_12` | New explicit direct file ladder. | `cargo test -p eos-e2e-test --features e2e --test pressure file_ops_ladder_1_3_6_12 -- --nocapture` | `pressure-ladder-file` |
| `ephemeral_exec_ladder_1_3_6_12` | New explicit exec ladder, correctness over strict latency. | `cargo test -p eos-e2e-test --features e2e --test pressure ephemeral_exec_ladder_1_3_6_12 -- --nocapture` | `pressure-ladder-exec` |
| `command_sessions_ladder_1_3_6_12` | New start/cancel/drain ladder. | `cargo test -p eos-e2e-test --features e2e --test pressure command_sessions_ladder_1_3_6_12 -- --nocapture` | `pressure-ladder-command` |
| `occ_ladder_1_3_6_12` | New disjoint and conflict OCC ladder. | `cargo test -p eos-e2e-test --features e2e --test pressure occ_ladder_1_3_6_12 -- --nocapture` | `pressure-ladder-occ` |
| `plugin_refresh_ladder_1_3_6_12` | New heavy-profile plugin refresh ladder. | `cargo test -p eos-e2e-test --features e2e --test pressure plugin_refresh_ladder_1_3_6_12 -- --nocapture` | `pressure-ladder-plugin` |
| `isolated_handle_cap_ladder` | New: run high-cap profile or assert configured cap rejection at `6/12`. | `cargo test -p eos-e2e-test --features e2e --test pressure isolated_handle_cap_ladder -- --nocapture` | `pressure-isolated-cap` |
| `perf_resource_report_smoke` | New: writes one JSON artifact with timings/resources/leak counters. | `cargo test -p eos-e2e-test --features e2e --test pressure perf_resource_report_smoke -- --nocapture` | `pressure-resource-report` |

---

## 8. Adversarial Review Requirements

Before implementation starts:

1. Re-read the live Rust sources, not only `docs/architecture`, for plugin and
   daemon paths. Some architecture plugin paths can lag; prefer
   `sandbox/crates/eos-daemon/src/services/plugins/*`.
2. Use `eos-command-session` and `eos-runner` as the command-session truth. Do
   not plan around an `eos-terminal-pair` crate if it is absent in the checkout.
3. Decide the `setsid nohup` contract before writing that test. The test must
   encode the intended behavior, not accidentally document a leak.
4. Do not assert live OCC batching unless the path under test submits a
   non-atomic changeset. Current disjoint write tests prove correctness, not
   queue batching.
5. Treat `orphan_layer_count == 0` and `missing_layer_count == 0` as
   supplemental until backed by real enumeration.
6. Keep write scopes disjoint if multiple agents implement this spec in
   parallel: README files, harness config, OCC/LayerStack, workspace/session,
   plugin/daemon, and pressure can be separate workstreams.

---

## 9. Verification Ladder

Baseline non-live check:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo test -p eos-e2e-test -- --list
cargo check -p eos-e2e-test --all-targets
```

Live build prerequisite:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo run -p xtask -- package --target x86_64-unknown-linux-musl --out-dir dist
```

Focused live modules:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo test -p eos-e2e-test --features e2e --test occ -- --nocapture
cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace -- --nocapture
cargo test -p eos-e2e-test --features e2e --test isolated_workspace -- --nocapture
cargo test -p eos-e2e-test --features e2e --test layerstack -- --nocapture
cargo test -p eos-e2e-test --features e2e --test daemon -- --nocapture
cargo test -p eos-e2e-test --features e2e --test plugin -- --nocapture
cargo test -p eos-e2e-test --features e2e --test pressure -- --nocapture
```

Supporting crate checks for lower-level changes:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/sandbox
cargo test -p eos-occ --all-targets
cargo test -p eos-layerstack --all-targets
cargo test -p eos-overlay --all-targets
cargo test -p eos-ephemeral-workspace --all-targets
cargo test -p eos-command-session --all-targets
cargo check -p eos-daemon --all-targets
```

---

## 10. Acceptance Criteria

1. All 8 module `readme.md` files exist and use the required structure.
2. Every README checklist item is covered by at least one test-case row.
3. README test names match the live `cargo test -- --list` inventory or are
   explicitly marked as planned.
4. Fast live module runs pass under Docker with the default dask image.
5. Heavy/perf runs are selectable without changing source code.
6. `1/3/6/12` concurrency comparisons exist for direct file ops, ephemeral exec,
   command sessions, OCC pressure, and plugin refresh where valid.
7. Isolated workspace pressure either uses a high-cap config or asserts cap
   rejection above the configured cap.
8. Resource-critical tests assert both correctness and cleanup: no leaked
   command sessions, active leases return to zero, overlay scratch is removed,
   plugin service processes are reaped, and storage growth stays bounded.
9. Performance-critical tests emit JSON artifacts before any strict regression
   threshold is added.
10. The suite remains Docker-only and protocol-first.
