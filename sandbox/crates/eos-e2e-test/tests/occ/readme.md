# occ

## Overview

OCC tests cover the workspace publish gate in front of LayerStack through `api.v1.write_file`, `api.v1.edit_file`, `api.v1.read_file`, and audit pull helpers against a live `eosd`. The module config is `crates/eos-e2e-test/tests/occ/config/default.test.yml`. This module is one unified E2E contract for route correctness, conflict semantics, audit accounting, and concurrent publish behavior.

## Checklist

- [ ] occ-git-drop: `.git/**` writes return a committed success envelope without published paths, stay unreadable, and must not advance manifest-visible state.
- [ ] occ-gitignored-direct: gitignored paths route direct, bypass stale-base gated OCC checks, and remain whole-payload safe under same-path direct-write races.
- [ ] occ-tracked-gated: non-gitignored paths route gated, publish through OCC, and expose route timing counters.
- [ ] occ-disjoint-merge: concurrent tracked writes to disjoint paths all commit and remain readable without lost updates.
- [ ] occ-conflict-report: concurrent same-path writes surface structured commit, conflict, or error payloads and leave coherent final content from one whole writer.
- [ ] occ-edit-anchor-errors: missing, stale, or ambiguous edit anchors return structured no-op conflict payloads without partial file mutation.
- [ ] occ-audit-accounting: successful publish and conflict/rejection paths emit coherent audit or route accounting signals.
- [ ] occ-result-catalog: committed, rejected, dropped, and edit-conflict FileResult statuses keep stable wire names and reasons.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `occ-route-gating-and-direct-paths` | Covers `git_writes_are_dropped_and_unreadable`, `gitignored_writes_bypass_the_occ_gate`, and `concurrent_gitignored_same_path_direct_writes`: `.git/**` drops stay unreadable, ignored writes route direct, tracked siblings route gated, route counters remain protocol-visible, and same-path ignored races leave one whole writer payload. | `cargo test -p eos-e2e-test --features e2e --test occ gating -- --nocapture` | `occ-git-drop`, `occ-gitignored-direct`, `occ-tracked-gated`, `occ-audit-accounting`, `occ-result-catalog` |
| `occ-concurrent-publish-and-conflict-semantics` | Covers `concurrent_disjoint_writes`, `concurrent_conflicting_writes`, and `retry_budget_3x_surfaces_coherent_result`: disjoint tracked writes all publish, same-path races return structured outcomes, retry-budget pressure still leaves final content as one whole writer payload. | `cargo test -p eos-e2e-test --features e2e --test occ merge -- --nocapture` | `occ-disjoint-merge`, `occ-conflict-report`, `occ-result-catalog` |
| `occ-edit-conflict-and-result-catalog` | Covers `edit_overlap_conflict`, `edit_anchor_errors_do_not_publish_or_advance_manifest`, and `route_fileresult_catalog`: ambiguous and missing edit anchors, create-only rejection, missing edit conflicts, committed writes, rejected writes, no changed paths, unchanged content, unchanged manifest depth, and stable conflict reasons. | `cargo test -p eos-e2e-test --features e2e --test occ merge -- --nocapture` | `occ-edit-anchor-errors`, `occ-audit-accounting`, `occ-result-catalog` |
| `occ-publish-audit-accounting` | Covers `publish_accounting` plus the route counters from `gitignored_writes_bypass_the_occ_gate`: successful tracked publishes emit nonempty `changed_paths`, `occ.publish` audit events, and direct/gated timing counters. | `cargo test -p eos-e2e-test --features e2e --test occ -- --nocapture` | `occ-tracked-gated`, `occ-audit-accounting` |
