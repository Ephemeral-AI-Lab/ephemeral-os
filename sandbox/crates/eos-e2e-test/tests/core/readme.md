# core

## Overview

`core` owns the protocol-first smoke and contract surface for live `eosd`: runtime readiness, workspace binding/base setup, direct file ops, foreground exec, envelope guards, and basic audit/metrics visibility. It exercises daemon ops including `api.runtime.ready`, `api.v1.heartbeat`, `api.workspace.binding`, `api.layer_metrics`, `api.audit.snapshot`, `api.ensure_workspace_base`, `api.build_workspace_base`, `api.commit_to_workspace`, `api.v1.read_file`, `api.v1.write_file`, `api.v1.edit_file`, `api.v1.exec_command`, and raw malformed/auth envelope paths. Module config: `crates/eos-e2e-test/tests/core/config/default.test.yml`.

## Checklist

- [ ] core-runtime-base: Runtime readiness, probes, workspace binding, base-layer metrics, rebuild timing, heartbeat idle state, and base audit fields remain protocol-visible and coherent.
- [ ] core-host-default-layer-stack-root: Host and protocol workspace setup inject the LayerStack root from workspace configuration without requiring a separate caller routing parameter.
- [ ] core-workspace-commit: Committing the LayerStack view to the workspace survives a base rebuild and keeps manifest audit fields aligned with the response.
- [ ] core-envelope-guards: Unknown ops, malformed frames, oversized requests, bad or missing auth, and isolated-mode plugin-family ops return deterministic structured errors.
- [ ] core-direct-file-ops: Direct read/write/edit paths publish through OCC, bypass overlay leasing, expose direct timing counters, and keep lease counts at zero under repeated writes.
- [ ] core-file-error-catalog: Missing reads, edit anchor failures, ambiguous edit counts, create-only conflicts, and file size limits return deterministic no-publish error payloads.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `core-runtime-and-workspace-state` | Runs the unified runtime/base/workspace scenario: `setup_readiness_metrics_and_audit_are_protocol_visible`, `runtime_ready_handshake`, `ensure_base_creates_single_base_layer`, `ensure_base_idempotent`, `build_base_reset_rebuilds`, `workspace_binding_roundtrip`, `heartbeat_inflight_idle_zero`, and `commit_to_workspace_survives_protocol_rebuild` together prove readiness, base metrics, audit evidence, idle command state, and rebuild-safe workspace commits. | `cargo test -p eos-e2e-test --features e2e --test core -- --nocapture` | `core-runtime-base`, `core-workspace-commit` |
| `core-envelope-and-error-catalog` | Groups the protocol guard and deterministic error catalog: `unknown_op_rejected`, `bad_json_rejected`, `oversized_request_rejected`, `unauthorized_tcp_rejected`, `forbidden_in_isolated_workspace_rejected`, `read_nonexistent`, `read_nonexistent_reports_absent`, `edit_anchor_not_found`, `edit_count_mismatch`, `edit_error_catalog_anchor_not_found_and_count_mismatch`, `write_create_only_conflict`, `read_max_bytes_guard`, and `write_max_file_bytes_guard`. | `cargo test -p eos-e2e-test --features e2e --test core -- --nocapture` | `core-envelope-guards`, `core-file-error-catalog`, `core-direct-file-ops` |
| `core-direct-file-occ-path` | Groups direct file correctness and resource behavior: `direct_file_ops_round_trip_through_protocol`, `write_read_roundtrip`, `write_publishes_changed_paths`, `edit_search_replace_applied`, `edit_replace_all`, `fast_path_write_edit_emit_no_overlay_or_lease_audit`, `fast_path_surfaces_occ_and_read_timings`, `repeated_fast_path_writes_keep_leases_zero`, and `direct_file_ops_concurrency_ladder` cover OCC publish, timing counters, no overlay lease leakage, and concurrent readback across the configured `1/3/6/12` ladder. | `cargo test -p eos-e2e-test --features e2e --test core -- --nocapture` | `core-direct-file-ops`, `core-file-error-catalog`, `core-runtime-base` |
