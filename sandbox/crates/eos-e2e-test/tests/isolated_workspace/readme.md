# isolated_workspace

## Overview

This module owns the unified live E2E contract for isolated-session lifecycle, private upperdir routing, SetNs namespace teardown, network isolation, and file-tool routing while a caller is isolated. It exercises daemon ops `api.isolated_workspace.enter`, `api.isolated_workspace.status`, `api.isolated_workspace.exit`, `api.v1.read_file`, `api.v1.write_file`, `api.v1.edit_file`, `api.v1.exec_command`, and `api.v1.command.cancel`. Module config lives at `crates/eos-e2e-test/tests/isolated_workspace/config/default.test.yml`.

## Checklist

- [ ] isolated_workspace-lifecycle-pin: Enter and status expose the same manifest pin, and exit unregisters the handle, releases the lease, and reports holder/cgroup teardown.
- [ ] isolated_workspace-private-persistence: Isolated writes and reads persist across tool calls while the handle is open and report isolated workspace/mutation fields.
- [ ] isolated_workspace-no-publish: Isolated write, edit, and exec paths do not publish through OCC or release public LayerStack leases.
- [ ] isolated_workspace-discard-exit: Exit evicts the private upperdir, and public or ephemeral reads cannot see isolated-only files afterward.
- [ ] isolated_workspace-network-isolation: A dedicated isolated netns and veth allow an isolated caller to bind a port already held in the container netns.
- [ ] isolated_workspace-same-netns-conflict: Reusing a port in the same namespace conflicts unless the isolated session has exited and cleaned the prior command.
- [ ] isolated_workspace-exit-cleanup: Exit or cancel drains long-lived isolated command sessions and removes namespace resources so re-enter starts cleanly.
- [ ] isolated_workspace-tool-routing-contract: File tools in isolated mode return explicit workspace, mode, mutation, publish, and conflict fields, then route back to ephemeral after exit.
- [ ] isolated_workspace-exec-private-discard: Exec writes inside isolated mode remain private while open, then disappear on exit without OCC publication.
- [ ] isolated_workspace-lifecycle-gates: Enter rejects active sandbox-bound background work, repeated enter reports an already-open or lifecycle-in-progress state, and exit drain timeout/retry remains stable.
- [ ] isolated_workspace-network-peer-block: Isolated callers can reach their own loopback service but cannot reach peer isolated sessions through namespace or bridge addresses.
- [ ] isolated_workspace-audit-trail: Enter, exit, and isolated tool calls emit lifecycle audit fields, teardown timings, discarded-byte counters, and orphan-check evidence.
- [ ] isolated_workspace-ttl-eviction: Short test config TTL evicts idle isolated sessions and preserves coherent status/list-open behavior.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `isolated_workspace_lifecycle_and_teardown` | Groups `enter_status_exit_pin_and_teardown`, `isolated_enter_status_reports_manifest_pin`, `isolated_exit_discards_private_upperdir`, `isolated_exit_reports_dedicated_netns`, and `iws_same_port_discard`: enter/status pins must match, exit must unregister and tear down resources, and re-enter must start cleanly. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace -- --nocapture` | `isolated_workspace-lifecycle-pin`, `isolated_workspace-discard-exit`, `isolated_workspace-exit-cleanup` |
| `isolated_workspace_private_file_routing` | Groups `isolated_write_is_discarded_on_exit`, `isolated_read_after_exit_routes_ephemeral`, `isolated_write_response_fields`, and `isolated_read_file_sees_private_upperdir`: private file state is visible while open, carries isolated response fields, disappears after exit, and later reads route ephemeral. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace -- --nocapture` | `isolated_workspace-private-persistence`, `isolated_workspace-discard-exit`, `isolated_workspace-tool-routing-contract` |
| `isolated_workspace_no_publish_contract` | Groups `isolated_write_does_not_publish_or_release_lease` and `isolated_edit_conflict_response_fields`: isolated write/edit paths must avoid OCC publication and public lease release while preserving explicit conflict response fields. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace -- --nocapture` | `isolated_workspace-no-publish`, `isolated_workspace-tool-routing-contract` |
| `isolated_workspace_network_port_matrix` | Groups `cross_mode_same_port_no_conflict`, `same_mode_same_port_conflicts`, `isolated_exit_reports_dedicated_netns`, and `isolated_to_isolated_same_port_matrix`: cross-namespace same-port binds must work, same-namespace binds must conflict, netns/veth teardown must be visible, and two isolated callers can bind the same port independently. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace -- --nocapture` | `isolated_workspace-network-isolation`, `isolated_workspace-same-netns-conflict`, `isolated_workspace-exit-cleanup` |
| `isolated_workspace_exec_private_discard` | Adds `isolated_exec_write_is_private_and_discarded`: `api.v1.exec_command` writes inside isolated mode are readable while open, never OCC-published, and gone after exit. | `cargo test -p eos-e2e-test --features e2e --test isolated_workspace -- --nocapture` | `isolated_workspace-exec-private-discard`, `isolated_workspace-no-publish`, `isolated_workspace-discard-exit` |
