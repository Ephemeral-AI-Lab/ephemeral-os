# pressure

## Overview

`pressure` owns cross-subsystem stress coverage for live `eosd`: concurrent direct file ops, reads, ephemeral exec, LayerStack auto-squash, OCC disjoint publish, command-session cancellation, plugin refresh, isolated handle pressure, post-cancel daemon readiness, and JSON resource reports. It exercises daemon ops including `api.v1.write_file`, `api.v1.read_file`, `api.v1.exec_command`, `api.v1.command_cancel`, `api.v1.command_session_count`, `api.layer_metrics`, `api.runtime.ready`, `api.plugin.ensure`, `api.plugin.status`, `plugin.generic.query`, and `api.isolated_workspace.*`. Module config: `crates/eos-e2e-test/tests/pressure/config/default.test.yml`. This is one unified E2E contract.

## Checklist

- [ ] pressure-ladder-file: Direct file write/read pressure passes at `1/3/6/12`, publishes coherent content, and does not leak active leases.
- [ ] pressure-ladder-exec: Ephemeral exec pressure passes at `1/3/6/12`, publishes changed workspace files, and releases overlay leases.
- [ ] pressure-ladder-command: Command sessions start, cancel, and drain at `1/3/6/12` with session count and active leases returning to zero.
- [ ] pressure-ladder-occ: OCC disjoint writes and same-path conflict pressure return coherent payloads at `1/3/6/12`.
- [ ] pressure-ladder-plugin: Plugin refresh/dispatch pressure remains coherent at configured levels and keeps refresh/process counts bounded.
- [ ] pressure-isolated-cap: Isolated handle pressure either runs under a high-cap config or asserts configured cap rejection above the default limit.
- [ ] pressure-squash-bound: Repeated overwrite pressure keeps manifest depth under the operational auto-squash target while preserving latest content.
- [ ] pressure-recovery-cleanup: Midflight cancel and cancel bursts leave daemon readiness intact and drain command sessions, active leases, and marker work.
- [ ] pressure-resource-report: E2E runs emit JSON summaries for latency shape, resource counters, and leak counters before strict regression thresholds are introduced.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `pressure-mixed-file-exec-ladder` | Groups `n_concurrent_mixed_ops`, `overlay_exec_publishes_file_back_to_layerstack`, `file_ops_ladder_1_3_6_12`, and `ephemeral_exec_ladder_1_3_6_12` to prove concurrent direct file and ephemeral exec pressure returns structured payloads, publishes readable content, and drains active leases. | `cargo test -p eos-e2e-test --features e2e --test pressure file_ops_ladder_1_3_6_12 -- --nocapture` | `pressure-ladder-file`, `pressure-ladder-exec`, `pressure-recovery-cleanup` |
| `pressure-layerstack-occ-contention` | Groups `write_storm_squash_under_load`, `layerstack_auto_squash_keeps_depth_bounded`, `occ_merges_concurrent_disjoint_protocol_writes`, and `occ_ladder_1_3_6_12` to prove repeated overwrites preserve latest content, auto-squash bounds manifest depth, and concurrent OCC writes remain coherent. | `cargo test -p eos-e2e-test --features e2e --test pressure occ_ladder_1_3_6_12 -- --nocapture` | `pressure-ladder-file`, `pressure-ladder-occ`, `pressure-squash-bound` |
| `pressure-command-cancel-drain` | Groups `daemon_recovers_after_midflight_cancel`, `cancel_burst_returns_sessions_and_leases_to_zero`, `cancel_storm`, and `command_sessions_ladder_1_3_6_12` so command pressure proves daemon readiness, structured cancel status, zero session count, and zero active leases after bursts. | `cargo test -p eos-e2e-test --features e2e --test pressure command_sessions_ladder_1_3_6_12 -- --nocapture` | `pressure-ladder-command`, `pressure-recovery-cleanup`, `pressure-resource-report` |
| `pressure-plugin-isolated-cap-matrix` | Groups `plugin_refresh_ladder_1_3_6_12` and `isolated_handle_cap_ladder` to prove plugin refresh/dispatch stays coherent under configured concurrency and isolated handle pressure either succeeds within cap or rejects above cap with a stable payload. | `cargo test -p eos-e2e-test --features e2e --test pressure plugin_isolated -- --nocapture` | `pressure-ladder-plugin`, `pressure-isolated-cap`, `pressure-recovery-cleanup` |
| `pressure-resource-report-and-leak-oracles` | `resource_report_smoke` emits one JSON artifact covering timing keys, resource counters, runtime readiness, plugin status, isolated-open state, and leak counters after file, exec, command-session, and OCC-backed write samples. | `cargo test -p eos-e2e-test --features e2e --test pressure resource_report_smoke -- --nocapture` | `pressure-resource-report`, `pressure-ladder-file`, `pressure-ladder-exec`, `pressure-ladder-command`, `pressure-ladder-occ`, `pressure-ladder-plugin`, `pressure-isolated-cap`, `pressure-squash-bound`, `pressure-recovery-cleanup` |
