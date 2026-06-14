# daemon

## Overview

`daemon` owns the live daemon control-plane contract: in-sandbox identity, response meta trace refs, built-in op registration, invocation registry accounting, heartbeat touch semantics, cancellation responses, and TTL reaping. It exercises daemon ops including `sandbox.runtime.ready`, `sandbox.call.count`, `sandbox.call.heartbeat`, `sandbox.call.cancel`, `sandbox.command.exec`, and every non-mutating entry in `BUILTIN_DAEMON_OPS`, with state-toggling ops covered by dedicated module tests. Module config lives at `crates/e2e-test/tests/daemon/config/default.test.yml`, which shortens `daemon.inflight` TTL/reaper intervals for bounded live checks.

## Checklist

- [ ] daemon-ready-identity: Runtime readiness exposes daemon PID, uptime, successful probes, and envelope status/meta on success and error responses.
- [ ] daemon-op-registry: Built-in daemon op names are wire-routed and unregistered names reject with `unknown_op`.
- [ ] daemon-inflight: Concurrent background invocations are visible through inflight accounting and joined without outliving the node lease.
- [ ] daemon-heartbeat: Heartbeat `touched` counts distinguish live invocation ids from bogus ids without mutating idle state.
- [ ] daemon-cancel-control: Cancel responses are coherent for unknown and live inflight invocation ids, including already-done and cancelled semantics.
- [ ] daemon-ttl-reaper: Short TTL/reaper daemon config removes stale inflight state and proves cleanup through registry counts.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `daemon-identity-registry-wire-contract` | Groups `runtime_ready_exposes_daemon_identity`, `every_response_carries_trace_meta`, and `every_builtin_op_is_wire_routed` to prove daemon identity, response meta trace refs, registered op routing, and `unknown_op` rejection over the live wire. | `cargo run -p e2e-test --bin e2e-runner -- --suites daemon --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `daemon-ready-identity`, `daemon-op-registry` |
| `daemon-inflight-heartbeat-cancel-control` | Groups `inflight_count_observes_concurrent_background_invocations`, `heartbeat_touched_counts_only_bogus_as_zero`, `heartbeat_touched_distinguishes_live_from_bogus`, `cancel_unknown_invocation_returns_done_response`, and `live_cancel_of_inflight_sets_cancelled` so the registry count, heartbeat touch, unknown cancel, live cancel, and join-cleanup behavior are validated together. | `cargo run -p e2e-test --bin e2e-runner -- --suites daemon --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `daemon-inflight`, `daemon-heartbeat`, `daemon-cancel-control` |
| `daemon-stale-inflight-reaper-contract` | Uses `inflight_ttl_reaper_cleanup`: a long background exec is removed by the short module-local TTL/reaper config before natural command completion, then heartbeat and cancel observe deregistered state. | `cargo run -p e2e-test --bin e2e-runner -- --suites daemon --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `daemon-ttl-reaper`, `daemon-inflight`, `daemon-cancel-control`, `daemon-heartbeat` |
