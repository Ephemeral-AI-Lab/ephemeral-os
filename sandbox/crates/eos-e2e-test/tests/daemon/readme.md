# daemon

## Overview

`daemon` owns the live daemon control-plane contract: in-sandbox identity, dispatch timings, built-in op registration, invocation registry accounting, heartbeat touch semantics, cancellation envelopes, audit pagination/reset, TTL reaping, and dynamic plugin background registry participation. It exercises daemon ops including `api.runtime.ready`, `api.v1.inflight_count`, `api.v1.heartbeat`, `api.v1.cancel`, `api.v1.exec_command`, `api.audit.pull`, `api.audit.reset_floor`, `api.plugin.ensure`, `api.plugin.status`, dynamic `plugin.daemonplug.query`, and every non-mutating entry in `BUILTIN_DAEMON_OPS`, with state-toggling ops covered by dedicated module tests. Module config: `crates/eos-e2e-test/tests/daemon/config/default.test.yml`, which enables the audit reset gate and shortens `daemon.inflight` TTL/reaper timing for bounded live checks.

## Checklist

- [ ] daemon-ready-identity: Runtime readiness exposes daemon PID, uptime, successful probes, and dispatch timing counters on success and error envelopes.
- [ ] daemon-op-registry: Built-in daemon op names are wire-routed and unregistered names reject with `unknown_op`.
- [ ] daemon-inflight: Concurrent background invocations are visible through inflight accounting and joined without outliving the node lease.
- [ ] daemon-heartbeat: Heartbeat `touched` counts distinguish live invocation ids from bogus ids without mutating idle state.
- [ ] daemon-cancel-control: Cancel envelopes are coherent for unknown and live inflight invocation ids, including already-done and cancelled semantics.
- [ ] daemon-audit: Audit pull, pagination, cursor/floor behavior, and the config-gated reset hook are explicitly covered without relying on transient global state.
- [ ] daemon-audit-scope-boundary: Daemon audit tests cover ring pagination/reset behavior while isolated lifecycle event fields and orphan checks stay owned by isolated workspace tests.
- [ ] daemon-ttl-reaper: Short TTL/reaper daemon config removes stale inflight state and proves cleanup through registry counts.
- [ ] daemon-plugin-control: Dynamic plugin operations marked `background: true` participate in inflight, heartbeat, and cancel envelope control. Plugin process-group cancellation is not asserted: `sandbox/crates/eos-daemon/src/transport/server.rs` registers every background request, while `sandbox/crates/eos-daemon/src/services/overlay/mod.rs` is the only current `register_process_group` caller.
- [ ] daemon-plugin-background-boundary: Dynamic plugin background requests use daemon inflight, heartbeat, and cancel control while plugin publish, setup failure, and worker replacement semantics stay plugin-owned.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `daemon-identity-registry-wire-contract` | Groups `runtime_ready_exposes_daemon_identity`, `every_response_carries_dispatch_timings`, and `every_builtin_op_is_wire_routed` to prove daemon identity, dispatch timings, registered op routing, and `unknown_op` rejection over the live wire. | `cargo test -p eos-e2e-test --features e2e --test daemon -- --nocapture` | `daemon-ready-identity`, `daemon-op-registry` |
| `daemon-inflight-heartbeat-cancel-control` | Groups `inflight_count_observes_concurrent_background_invocations`, `heartbeat_touched_counts_only_bogus_as_zero`, `heartbeat_touched_distinguishes_live_from_bogus`, `cancel_unknown_invocation_returns_done_envelope`, and `live_cancel_of_inflight_sets_cancelled` so the registry count, heartbeat touch, unknown cancel, live cancel, and join-cleanup behavior are validated together. | `cargo test -p eos-e2e-test --features e2e --test daemon -- --nocapture` | `daemon-inflight`, `daemon-heartbeat`, `daemon-cancel-control` |
| `daemon-audit-cursor-reset-contract` | Uses `audit_pull_paginates_and_reset_floor_is_enabled_by_config`: audit pulls paginate with `limit=1`, cursor filtering is strictly after the requested sequence, empty future pulls preserve the requested floor, and the module config enables the reset gate. | `cargo test -p eos-e2e-test --features e2e --test daemon audit_pull_paginates_and_reset_floor_is_enabled_by_config -- --nocapture` | `daemon-audit`, `daemon-ready-identity` |
| `daemon-stale-inflight-reaper-contract` | Uses `inflight_ttl_reaper_cleanup`: a long background exec is removed by the short module-local TTL/reaper config before natural command completion, then heartbeat and cancel observe deregistered state. | `cargo test -p eos-e2e-test --features e2e --test daemon inflight_ttl_reaper_cleanup -- --nocapture` | `daemon-ttl-reaper`, `daemon-inflight`, `daemon-cancel-control`, `daemon-heartbeat` |
| `daemon-plugin-background-control-contract` | Uses `background_plugin_operation_control`: a slow dynamic plugin request marked `background: true` is counted, heartbeat-visible, and found by cancel. Process-group kill semantics are not claimed because plugin PPC dispatch does not currently register a process group. | `cargo test -p eos-e2e-test --features e2e --test daemon background_plugin_operation_control -- --nocapture` | `daemon-plugin-control`, `daemon-inflight`, `daemon-heartbeat`, `daemon-cancel-control` |
