# ephemeral_workspace

## Overview

This module owns the unified live E2E contract for ephemeral workspace routing, overlay exec, LayerStack lease cleanup, OCC publish, and command-session lifecycle behavior. It exercises daemon ops `api.v1.exec_command`, `api.v1.read_file`, `api.v1.write_file`, `api.v1.command_session_count`, `api.v1.command.collect_completed`, `api.v1.write_stdin`, and `api.v1.command.cancel`. Module config lives at `crates/eos-e2e-test/tests/ephemeral_workspace/config/default.test.yml`.

## Checklist

- [ ] ephemeral_workspace-per-call-overlay: Every foreground/background exec derives an ephemeral overlay over the latest workspace manifest and only finalized in-workspace deltas become workspace state.
- [ ] ephemeral_workspace-outside-direct-fs: Writes outside the workspace are excluded from OCC `changed_paths` and remain direct container filesystem effects.
- [ ] ephemeral_workspace-upperdir-delta: Overlay upperdir accounting stays proportional to modified bytes and does not copy large lowerdir inputs.
- [ ] ephemeral_workspace-overlay-cleanup: Completed foreground exec releases LayerStack leases, removes overlay scratch, and returns active lease metrics to zero.
- [ ] ephemeral_workspace-command-session-lifecycle: Background command sessions remain running until the whole process group exits and collect exactly one terminal result.
- [ ] ephemeral_workspace-command-session-termination: `write_stdin` termination and command cancel reap all same-pgid descendants without session-count or marker leaks.
- [ ] ephemeral_workspace-occ-publish-readback: In-workspace exec writes publish through daemon-owned OCC and are readable through `api.v1.read_file`.
- [ ] ephemeral_workspace-stale-exec-conflict: A long-running exec from a stale snapshot cannot silently overwrite newer direct file content.
- [ ] ephemeral_workspace-route-edges: Multi-path shell capture, command/publish timing fields, and read-intent no-publish behavior stay observable through daemon responses and audit events.
- [ ] ephemeral_workspace-policy-denials: Host-prefix writes, cwd escapes, and workspace-destructive shell commands are rejected before any workspace publish when the daemon owns those protocol errors.
- [ ] ephemeral_workspace-whiteout-resync: File deletes, replacement writes, deep-manifest pre-mount squash, and foreign-published workspace changes round-trip through overlay readback.
- [ ] ephemeral_workspace-cancel-no-partial-publish: Cancelled background execs do not publish partial workspace mutation and still release overlay leases.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `ephemeral_workspace_overlay_publish_and_cleanup` | Groups `exec_simple`, `exec_write_outside_workspace_is_not_captured`, `foreground_exec_recycles_overlay_scratch`, `exec_upperdir_captures_only_the_delta`, and `exec_overlay_mount_publishes_changed_paths`: validates per-call overlay derivation, direct `/tmp` exclusion, delta-sized upperdir accounting, scratch/lease cleanup, OCC publish, and readback. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace -- --nocapture` | `ephemeral_workspace-per-call-overlay`, `ephemeral_workspace-outside-direct-fs`, `ephemeral_workspace-upperdir-delta`, `ephemeral_workspace-overlay-cleanup`, `ephemeral_workspace-occ-publish-readback` |
| `ephemeral_workspace_route_edges_and_read_intent` | Uses `exec_multi_path_route_timings_and_read_intent_no_publish`: a multi-path shell write reports every changed path plus command and OCC timings, while a read-only exec over the same paths publishes no changes. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace exec_multi_path_route_timings_and_read_intent_no_publish -- --nocapture` | `ephemeral_workspace-route-edges`, `ephemeral_workspace-occ-publish-readback` |
| `ephemeral_workspace_whiteout_resync` | Uses `overlay_delete_replacement_write_and_foreign_publish_are_readable`: shell file deletes and replacement writes round-trip through overlay capture, then a later foreign caller publish is readable through the same LayerStack root. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace overlay_delete_replacement_write_and_foreign_publish_are_readable -- --nocapture` | `ephemeral_workspace-whiteout-resync` |
| `ephemeral_workspace_process_group_lifecycle` | Groups `lingering_child_keeps_session_running` and `session_completes_only_after_all_subprocesses_exit`: a same-pgid child keeps the session running, collection waits for all subprocesses, and the session count drains after terminal collection. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace -- --nocapture` | `ephemeral_workspace-command-session-lifecycle` |
| `ephemeral_workspace_process_group_termination` | Groups `write_stdin_terminate_kills_whole_session` and `cancel_reaps_lingering_descendant`: stdin termination and explicit cancel must kill the whole process group, remove markers, and leave no session-count leak. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace -- --nocapture` | `ephemeral_workspace-command-session-termination` |
| `ephemeral_workspace_cancel_no_partial_publish` | Uses `cancelled_background_exec_does_not_publish_partial_workspace_mutation`: a background command cancelled before its workspace write drains its session and leases without publishing the later file. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace cancelled_background_exec_does_not_publish_partial_workspace_mutation -- --nocapture` | `ephemeral_workspace-cancel-no-partial-publish`, `ephemeral_workspace-command-session-termination` |
| `ephemeral_workspace_stale_exec_conflict` | Adds `long_running_exec_conflicts_after_direct_write`: an exec held on an old snapshot cannot silently overwrite newer direct file content, and the newer content remains readable after stale finalization. | `cargo test -p eos-e2e-test --features e2e --test ephemeral_workspace -- --nocapture` | `ephemeral_workspace-stale-exec-conflict` |
