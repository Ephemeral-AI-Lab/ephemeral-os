# daemon

## Overview

`daemon` owns the live daemon control-plane contract: in-sandbox identity, dispatch timings, built-in op registration, invocation registry accounting, heartbeat touch semantics, and cancellation envelopes. It exercises daemon ops including `api.runtime.ready`, `api.v1.inflight_count`, `api.v1.heartbeat`, `api.v1.cancel`, `api.v1.exec_command`, and every non-mutating entry in `BUILTIN_DAEMON_OPS`, with state-toggling ops covered by dedicated module tests. Module config: `crates/eos-e2e-test/tests/daemon/config/default.test.yml`. This is one unified E2E contract; audit pagination, TTL reaper, and plugin-control rows are planned coverage gaps.

## Checklist

- [ ] daemon-ready-identity: Runtime readiness exposes daemon PID, uptime, successful probes, and dispatch timing counters on success and error envelopes.
- [ ] daemon-op-registry: Built-in daemon op names are wire-routed and unregistered names reject with `unknown_op`.
- [ ] daemon-inflight: Concurrent background invocations are visible through inflight accounting and joined without outliving the node lease.
- [ ] daemon-heartbeat: Heartbeat `touched` counts distinguish live invocation ids from bogus ids without mutating idle state.
- [ ] daemon-cancel-control: Cancel envelopes are coherent for unknown and live inflight invocation ids, including already-done and cancelled semantics.
- [ ] daemon-audit: Audit pull, pagination, cursor/floor behavior, and test reset hooks are explicitly covered without relying on transient global state.
- [ ] daemon-ttl-reaper: Short TTL/reaper daemon config removes stale inflight state and proves cleanup through registry counts.
- [ ] daemon-plugin-control: Background plugin/PPC operations either participate in inflight/heartbeat/cancel control or document unsupported behavior with a stable response.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `daemon-identity-registry-wire-contract` | Groups `runtime_ready_exposes_daemon_identity`, `every_response_carries_dispatch_timings`, and `every_builtin_op_is_wire_routed` to prove daemon identity, dispatch timings, registered op routing, and `unknown_op` rejection over the live wire. | `cargo test -p eos-e2e-test --features e2e --test daemon -- --nocapture` | `daemon-ready-identity`, `daemon-op-registry` |
| `daemon-inflight-heartbeat-cancel-control` | Groups `inflight_count_observes_concurrent_background_invocations`, `heartbeat_touched_counts_only_bogus_as_zero`, `heartbeat_touched_distinguishes_live_from_bogus`, `cancel_unknown_invocation_returns_done_envelope`, and `live_cancel_of_inflight_sets_cancelled` so the registry count, heartbeat touch, unknown cancel, live cancel, and join-cleanup behavior are validated together. | `cargo test -p eos-e2e-test --features e2e --test daemon -- --nocapture` | `daemon-inflight`, `daemon-heartbeat`, `daemon-cancel-control` |
| `daemon-audit-cursor-reset-contract` | Planned grouped audit scenario: `audit_pull_paginates_and_baselines` and `isolated_workspace_test_reset_behavior` must prove audit pagination, cursor/floor behavior, and test-reset effects without relying on transient global state. | `cargo test -p eos-e2e-test --features e2e --test daemon audit_pull_paginates_and_baselines -- --nocapture` | `daemon-audit`, `daemon-ready-identity` |
| `daemon-stale-inflight-reaper-contract` | Planned TTL/reaper scenario: `inflight_ttl_reaper_cleanup` must use short TTL/reaper daemon config, prove stale inflight entries are removed, and show cancel/heartbeat state stays coherent afterward. | `cargo test -p eos-e2e-test --features e2e --test daemon inflight_ttl_reaper_cleanup -- --nocapture` | `daemon-ttl-reaper`, `daemon-inflight`, `daemon-cancel-control`, `daemon-heartbeat` |
| `daemon-plugin-background-control-contract` | Planned plugin/PPC scenario: `background_plugin_operation_control` must either prove plugin background work participates in inflight, heartbeat, and cancel control or lock in a stable unsupported response. | `cargo test -p eos-e2e-test --features e2e --test daemon background_plugin_operation_control -- --nocapture` | `daemon-plugin-control`, `daemon-inflight`, `daemon-heartbeat`, `daemon-cancel-control` |
