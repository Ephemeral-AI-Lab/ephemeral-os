# core

## Overview

`core` owns the protocol-first smoke and contract surface for live `eosd`: runtime readiness, workspace binding/base setup, direct file ops, foreground exec, wire-message guards, and basic metrics visibility. It exercises daemon ops including `sandbox.runtime.ready`, `sandbox.call.heartbeat`, `sandbox.checkpoint.binding`, `sandbox.checkpoint.layer_metrics`, `sandbox.checkpoint.build_base`, `sandbox.checkpoint.commit_to_workspace`, `sandbox.file.read`, `sandbox.file.write`, `sandbox.file.edit`, `sandbox.command.exec`, and raw malformed/auth wire-message paths. Module config: `crates/e2e-test/tests/core/config/default.test.yml`.

## Checklist

- [ ] core-runtime-base: Runtime readiness, probes, workspace binding, base-layer metrics, rebuild trace events, heartbeat idle state, and base-binding fields remain protocol-visible and coherent.
- [ ] core-host-default-layer-stack-root: Host and protocol workspace setup inject the LayerStack root from workspace configuration without requiring a separate caller routing parameter.
- [ ] core-workspace-commit: Committing the LayerStack view to the workspace survives a base rebuild and keeps the committed content readable after the rebuild.
- [ ] core-wire-message-guards: Unknown ops, malformed frames, oversized requests, bad or missing auth, and isolated-mode plugin-family ops return deterministic structured errors.
- [ ] core-direct-file-ops: Direct read/write/edit paths publish through OCC, bypass overlay leasing, record route/OCC/file trace events, and keep lease counts at zero under repeated writes.
- [ ] core-file-error-catalog: Missing reads, edit anchor failures, ambiguous edit counts, create-only conflicts, and config-driven per-file size caps (`daemon.files`: 8 MiB read, 8 MiB write) return deterministic no-publish error payloads, while a write between the legacy 2 MiB and the configured 8 MiB cap publishes.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `core-runtime-and-workspace-state` | Runs the unified runtime/base/workspace scenario: `setup_readiness_and_metrics_are_protocol_visible`, `runtime_ready_handshake`, `acquire_setup_creates_single_base_layer`, `acquire_setup_binds_workspace_without_extra_step`, `build_base_reset_rebuilds`, `workspace_binding_roundtrip`, `heartbeat_inflight_idle_zero`, and `commit_to_workspace_survives_protocol_rebuild` together prove readiness, base metrics, idle command state, and rebuild-safe workspace commits. | `cargo run -p e2e-test --bin e2e-runner -- --suites core --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `core-runtime-base`, `core-workspace-commit` |
| `core-wire-message-and-error-catalog` | Groups the protocol guard and deterministic error catalog: `unknown_op_rejected`, `bad_json_rejected`, `oversized_request_rejected`, `unauthorized_tcp_rejected`, `forbidden_in_isolated_rejected`, `read_nonexistent`, `read_nonexistent_reports_absent`, `edit_anchor_not_found`, `edit_count_mismatch`, `edit_error_catalog_anchor_not_found_and_count_mismatch`, `write_create_only_conflict`, `read_max_bytes_guard`, `write_max_file_bytes_guard`, and `write_above_legacy_two_mib_cap_succeeds` (config-driven `daemon.files` caps: a 3 MiB write above the legacy 2 MiB cap publishes while over-cap read/write payloads are rejected). | `cargo run -p e2e-test --bin e2e-runner -- --suites core --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `core-wire-message-guards`, `core-file-error-catalog`, `core-direct-file-ops` |
| `core-direct-file-occ-path` | Groups direct file correctness and resource behavior: `direct_file_ops_round_trip_through_protocol`, `write_read_roundtrip`, `write_publishes_changed_paths`, `edit_search_replace_applied`, `edit_replace_all`, `fast_path_write_publishes_without_holding_a_lease`, `fast_path_records_occ_and_read_trace_events`, `repeated_fast_path_writes_keep_leases_zero`, and `direct_file_ops_concurrency_ladder` cover OCC publish, trace events and changed-path results, no overlay lease leakage, and concurrent readback across the configured `1/3/6/12` ladder. | `cargo run -p e2e-test --bin e2e-runner -- --suites core --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `core-direct-file-ops`, `core-file-error-catalog`, `core-runtime-base` |
