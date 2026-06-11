# eos-occ

## Overview

OCC tests cover the workspace publish gate in front of LayerStack through `api.v1.write_file`, `api.v1.edit_file`, `api.v1.read_file`, `api.v1.exec_command`, and `api.layer_metrics` against a live `eosd`. The module config is `crates/eos-e2e-test/tests/eos-occ/config/default.test.yml`. This module is one unified E2E contract for route correctness, conflict semantics, publish accounting, publish batching, and concurrent publish behavior.

## Checklist

- [ ] occ-git-drop: `.git/**` writes return a committed success envelope without published paths, stay unreadable, and must not advance manifest-visible state.
- [ ] occ-gitignored-direct: gitignored paths route direct, bypass stale-base gated OCC checks, and remain whole-payload safe under same-path direct-write races.
- [ ] occ-tracked-gated: non-gitignored paths route gated, publish through OCC, and expose route timing counters.
- [ ] occ-disjoint-merge: concurrent tracked writes to disjoint paths all commit and remain readable without lost updates.
- [ ] occ-conflict-report: concurrent same-path writes surface structured commit, conflict, or error payloads and leave coherent final content from one whole writer.
- [ ] occ-edit-anchor-errors: missing, stale, or ambiguous edit anchors return structured no-op conflict payloads without partial file mutation.
- [ ] occ-audit-accounting: successful publish and conflict/rejection paths emit coherent route accounting signals.
- [ ] occ-result-catalog: committed, rejected, dropped, and edit-conflict FileResult statuses keep stable wire names and reasons.
- [ ] occ-atomic-changeset-audit: Multi-path publishes are all-or-nothing on conflict, and conflict versus publish outcomes are mutually exclusive for one commit.
- [ ] occ-multi-write-batch: A single overlay operation that writes M disjoint files publishes one batched layer, so manifest depth grows by fewer than M while every captured path is published and readable (the reliably observable form of the CommitQueue batching invariant).
- [ ] occ-concurrent-edit: Concurrent `edit_file` operations on disjoint anchors in one file stay atomic and coherent with no torn, duplicated, or lost lines, and concurrent same-anchor edits resolve to exactly one winner with structured conflicts for the rest.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `occ-route-gating-and-direct-paths` | Covers `git_writes_are_dropped_and_unreadable`, `gitignored_writes_bypass_the_occ_gate`, and `concurrent_gitignored_same_path_direct_writes`: `.git/**` drops stay unreadable, ignored writes route direct, tracked siblings route gated, route counters remain protocol-visible, and same-path ignored races leave one whole writer payload. | `cargo test -p eos-e2e-test --features e2e --test eos-occ gating -- --nocapture` | `occ-git-drop`, `occ-gitignored-direct`, `occ-tracked-gated`, `occ-audit-accounting`, `occ-result-catalog` |
| `occ-concurrent-publish-and-conflict-semantics` | Covers `concurrent_disjoint_writes`, `concurrent_conflicting_writes`, and `retry_budget_3x_surfaces_coherent_result`: disjoint tracked writes all publish, same-path races return structured outcomes, retry-budget pressure still leaves final content as one whole writer payload. | `cargo test -p eos-e2e-test --features e2e --test eos-occ merge -- --nocapture` | `occ-disjoint-merge`, `occ-conflict-report`, `occ-result-catalog` |
| `occ-atomic-changeset-audit` | Uses `atomic_overlay_changeset_drops_all_paths_on_stale_conflict`: a stale multi-path overlay completion conflicts on one path, publishes no paths from the same atomic changeset, preserves newer direct content, and leaves the sibling path absent. | `cargo test -p eos-e2e-test --features e2e --test eos-occ atomic_overlay_changeset_drops_all_paths_on_stale_conflict -- --nocapture` | `occ-atomic-changeset-audit`, `occ-conflict-report`, `occ-audit-accounting` |
| `occ-edit-conflict-and-result-catalog` | Covers `edit_overlap_conflict`, `edit_anchor_errors_do_not_publish_or_advance_manifest`, and `route_fileresult_catalog`: ambiguous and missing edit anchors, create-only rejection, missing edit conflicts, committed writes, rejected writes, no changed paths, unchanged content, unchanged manifest depth, and stable conflict reasons. | `cargo test -p eos-e2e-test --features e2e --test eos-occ merge -- --nocapture` | `occ-edit-anchor-errors`, `occ-audit-accounting`, `occ-result-catalog` |
| `occ-publish-audit-accounting` | Covers `publish_accounting` plus the route counters from `gitignored_writes_bypass_the_occ_gate`: successful tracked publishes emit nonempty `changed_paths`, advance the manifest version, and surface direct/gated timing counters. | `cargo test -p eos-e2e-test --features e2e --test eos-occ -- --nocapture` | `occ-tracked-gated`, `occ-audit-accounting` |
| `occ-concurrent-edit-and-batch` | Covers `single_overlay_exec_batches_multi_file_writes_into_one_layer`, `concurrent_disjoint_anchor_edits_stay_atomic_and_coherent`, and `concurrent_same_anchor_edits_resolve_to_one_winner`: one overlay capture batches M disjoint writes into fewer than M layers; concurrent disjoint-anchor edits leave a coherent single-version file; concurrent same-anchor edits leave exactly one winner with structured losers. | `cargo test -p eos-e2e-test --features e2e --test eos-occ test_eos_occ_concurrent_contention -- --nocapture` | `occ-multi-write-batch`, `occ-concurrent-edit`, `occ-disjoint-merge`, `occ-conflict-report`, `occ-result-catalog` |
