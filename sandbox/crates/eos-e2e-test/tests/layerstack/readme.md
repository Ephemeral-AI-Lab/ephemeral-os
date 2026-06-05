# layerstack

## Overview

LayerStack tests cover daemon-owned workspace base rebuild, active lease pinning, auto-squash, storage cleanup, commit-to-workspace, and commit-to-git behavior through `api.layer_metrics`, `api.v1.write_file`, `api.v1.read_file`, `api.isolated_workspace.enter`, `api.isolated_workspace.status`, `api.isolated_workspace.exit`, `api.build_workspace_base`, `api.commit_to_workspace`, and `api.commit_to_git` against a live `eosd`. The module config is `crates/eos-e2e-test/tests/layerstack/config/default.test.yml`, including `daemon.layer_stack.auto_squash_max_depth: 8`. This module is one unified E2E contract for lease lifecycle, squash integrity, resource cleanup, workspace commits, and Git overlay commits.

## Checklist

- [ ] layerstack-base-rebuild: workspace base rebuild after commit is idempotent, materializes the merged view, and is visible through LayerStack metrics.
- [ ] layerstack-lease-open-close: isolated workspace enter and exit acquire and release active LayerStack leases with coherent status and audit signals.
- [ ] layerstack-lease-pin-under-squash: active leases keep their pinned manifest version and hash stable while public writes trigger squash pressure, then release to zero active leases.
- [ ] layerstack-lease-lifetime: lease exit reports a nonnegative lifetime and leaves no stale open status behind.
- [ ] layerstack-workspace-commit-collapse: commit-to-workspace collapses accumulated layers to the workspace base and preserves monotonic manifest versioning.
- [ ] layerstack-workspace-commit-audit: commit-to-workspace emits audit fields that match the response manifest version and CAS-shaped root hash.
- [ ] layerstack-squash-depth: auto-squash triggers under write pressure and keeps manifest depth within the configured or default bound.
- [ ] layerstack-multi-pin-squash-bound: multiple isolated callers keep stable pinned status while public squash pressure keeps the active manifest and layer dirs bounded.
- [ ] layerstack-squash-integrity: squash completion reduces input depth, keeps the head readable, reports a CAS-shaped hash, and does not fail under single-client growth.
- [ ] layerstack-storage-cleanup: repeated overwrites and superseded layer dirs stay bounded using storage bytes, layer dir counts, and supplemental orphan/missing metrics.
- [ ] layerstack-git-commit-overlay: commit-to-git after repeated squash commits the overlay snapshot, honors path filters, reports timing phases, and records bounded LayerStack depth.
- [ ] layerstack-projection-roundtrip: Workspace projection preserves delete and whiteout masking, symlinks, replacement writes, timing fields, and storage-writer lock integrity.
- [ ] layerstack-deferred-gc: Leased layer directories survive squash pressure until release, and garbage collection reclaims only unreferenced storage afterward.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `layerstack-lease-lifecycle-and-pinned-squash` | Covers `enter_acquires_lease`, `exit_releases_lease`, `lease_pins_layers_vs_squash`, `squash_keeps_multiple_pinned_statuses_while_live_manifest_collapses`, and `lease_hold_time_ordering`: enter/exit status, lease acquire/release audit, active lease cleanup, pinned manifest stability under public squash pressure, bounded active manifest depth and layer dirs, and nonnegative lease lifetime. | `cargo test -p eos-e2e-test --features e2e --test layerstack lease -- --nocapture` | `layerstack-lease-open-close`, `layerstack-lease-pin-under-squash`, `layerstack-lease-lifetime`, `layerstack-squash-depth`, `layerstack-multi-pin-squash-bound` |
| `layerstack-workspace-base-and-commit-contract` | Covers `commit_collapses_layers`, `commit_materializes_merged_view`, `commit_version_monotonic`, `commit_emits_audit`, and `workspace_base_rebuild_idempotent_metrics`: workspace commits collapse depth, rebuild exposes merged content, repeated rebuild metrics stay bounded, manifest versions stay monotonic, and audit matches response hashes. | `cargo test -p eos-e2e-test --features e2e --test layerstack commit -- --nocapture` | `layerstack-base-rebuild`, `layerstack-workspace-commit-collapse`, `layerstack-workspace-commit-audit`, `layerstack-storage-cleanup` |
| `layerstack-projection-roundtrip` | Uses `commit_projects_delete_symlink_and_replacement_write`: commit-to-workspace projection preserves delete masking, symlink target, replacement writes, and projection/rebuild timing fields. | `cargo test -p eos-e2e-test --features e2e --test layerstack commit_projects_delete_symlink_and_replacement_write -- --nocapture` | `layerstack-projection-roundtrip`, `layerstack-workspace-commit-collapse` |
| `layerstack-squash-integrity-and-depth` | Covers `auto_squash_triggers_past_depth`, `checkpoint_layer_reduces_result_depth`, `head_readable_after_squash`, `squash_cas_hash_is_protocol_visible`, `squash_not_raced_single_client`, and `deep_stack_repeated_squash`: squash triggers, bounded depth, reduced checkpoint depth, readable head content, CAS-shaped audit hash, no single-client squash failure, and deep-stack readability. | `cargo test -p eos-e2e-test --features e2e --test layerstack squash -- --nocapture` | `layerstack-squash-depth`, `layerstack-squash-integrity`, `layerstack-workspace-commit-audit` |
| `layerstack-storage-and-layer-dir-cleanup` | Covers `auto_squash_bounds_depth_to_configured_max`, `repeated_overwrite_keeps_storage_bounded`, `squash_reclaims_superseded_layer_dirs`, and support/legacy `squash_storage_no_orphan`: configured max-depth enforcement, bounded storage growth, layer-dir reclamation, and supplemental orphan/missing counters. | `cargo test -p eos-e2e-test --features e2e --test layerstack squash -- --nocapture` | `layerstack-squash-depth`, `layerstack-storage-cleanup` |
| `layerstack-git-overlay-commit-after-squash` | Covers `commit_to_git_commits_overlay_snapshot_after_repeated_squash`: repeated squash before Git commit, overlay worktree mode, committed blob content, path filtering, timing phases, and reported LayerStack depth. | `cargo test -p eos-e2e-test --features e2e --test layerstack commit_to_git_commits_overlay_snapshot_after_repeated_squash -- --nocapture` | `layerstack-git-commit-overlay`, `layerstack-squash-depth`, `layerstack-storage-cleanup` |
