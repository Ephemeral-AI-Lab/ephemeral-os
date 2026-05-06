# Phase 01 Workspace Base Live E2E Report

Date: 2026-05-06 UTC

## Scope

This run validates the phase-01 workspace-base contract: a real Daytona
sandbox with `/testbed` is imported into layer-stack manifest version 1, then
base reads, layer creation, materialization, concurrency, failure safety, and
squash behavior are measured against that imported base.

The report intentionally cites only `sandbox.live_e2e.phase01_workspace_base.v1`
artifacts. Older `live-e2e-integrated-*` and `live-e2e-phase3-*` artifacts are
not used as phase-01 evidence.

## Commands

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/workspace_base \
  -v -rs -s --tb=short
```

Result: `8 passed, 1 warning in 70.03 s`.

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_read_load.py \
  -v -rs -s --tb=short
```

Result: `1 passed, 1 warning in 12.29 s`.

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_public_api_conflicts.py \
  -v -rs -s --tb=short
```

Result: `5 passed, 1 warning in 28.48 s`.

## Artifacts

| Case | Artifact |
|---|---|
| 20 independent base builds | `.omc/results/live-e2e-phase01-workspace-base-base_import_concurrency_independent-20260506T160605Z.jsonl` |
| Same-root concurrent build race | `.omc/results/live-e2e-phase01-workspace-base-base_import_concurrency_same_root-20260506T160609Z.jsonl` |
| Base import correctness | `.omc/results/live-e2e-phase01-workspace-base-base_import_correctness-20260506T160611Z.jsonl` |
| Base import cost | `.omc/results/live-e2e-phase01-workspace-base-base_import_cost-20260506T160638Z.jsonl` |
| Failure safety | `.omc/results/live-e2e-phase01-workspace-base-base_import_failure_safety-20260506T160640Z.jsonl` |
| Layer creation speed | `.omc/results/live-e2e-phase01-workspace-base-layer_create_speed-20260506T160642Z.jsonl` |
| Snapshot assembly speed | `.omc/results/live-e2e-phase01-workspace-base-snapshot_assembly_speed-20260506T160653Z.jsonl` |
| Squash with base and leases | `.omc/results/live-e2e-phase01-workspace-base-squash_with_base_and_leases-20260506T160657Z.jsonl` |
| Compatibility public read load | `.omc/results/live-e2e-phase01-workspace-base-workspace_base_read_load-20260506T160718Z.jsonl` |

## Results

| Area | Evidence |
|---|---|
| Base identity | Every phase-01 summary has `base_manifest_version == 1` and matching `base_root_hash` / `active_root_hash`. |
| Import cost | 5 sequential rebuilds over a 174-file, 19.27 MB `/testbed` baseline. Runtime p50 `142.745 ms`; runtime p99 `144.766 ms`; wall p50 `523.162 ms`; wall p99 `545.075 ms`. |
| Import breakdown | Latest cost artifact records `workspace_base.collect_s`, `workspace_base.write_layer_s`, `workspace_base.write_manifest_s`, `workspace_base.write_binding_s`, and inventory count/byte fields. |
| 20 independent builds | Batch wall `3.535 s`; all 20 builds succeeded and produced one matching base hash. |
| Same-root race | Batch wall `1.001 s`; 1 caller succeeded, 19 failed closed with existing-base errors; final manifest version `1`; orphan staging dirs `0`. |
| Correctness | Raw `/testbed` inventory matched base-layer inventory; binary, symlink, empty-dir, unicode-path, and long-path fixtures round-tripped. |
| Failure safety | Special file, disappearing file, changing file, new file during import, stack root inside `/testbed`, and existing binding all failed closed without partial workspace truth. |
| Layer creation | Six workloads passed: 1 small file, 100 small files, 2 MiB file, 100 overwrites, 50 deletes, and mixed write/overwrite/delete/symlink/opaque-dir. Publish p50 `3.619 ms`; p99 `5.776 ms`; real `/testbed` remained unchanged. |
| Snapshot assembly | 52 cold/warm materialization rows across base-only plus append, overwrite, delete, symlink, and opaque-dir stacks at depths `0, 1, 5, 20, 100, 200`. Materialize p50 `68.452 ms`; p99 `329.727 ms`; max `466.012 ms`. |
| Squash and leases | No-lease squash preserved byte-equivalent active views at depths `5, 20, 100, 200` and reduced depth. Lease case preserved the leased A-state through squash and GC, then removed pinned layers only after lease release. Squash p50 `63.924 ms`; p99 `92.646 ms`; max `93.407 ms`. |
| Public read compatibility | 32 reads over 16 imported base paths succeeded. Runtime max `0.741 ms`; wall max `526.834 ms`. The smoke mutates the raw `/testbed` file after import and verifies public `read_file` still returns layer-stack base content. |
| Public API conflicts | 5 focused public API conflict tests over imported-base fixture files passed. Covered concurrent writes to the same existing base file, disjoint and overlapping edits, shell full-file tracked conflict with gitignored output, shell delete vs public write, and raw `/testbed` mutation isolation. |

## Notes

- The native probe launcher now selects Python `>= 3.10`, matching the runtime
  daemon launcher. This avoids importing the runtime bundle with an older
  `python3` binary that cannot parse `dataclass(kw_only=True)`.
- Workspace-base import now performs a quiescence rescan before publishing
  `manifest.json` and `workspace.json`, so files appearing after the initial
  scan fail closed instead of creating an incomplete base.

## Coverage Assessment

Phase 01 is sufficient for base-import sign-off. It proves base import
cost/correctness/failure safety, 20 independent base builds, same-root
base-build race behavior, layer creation over imported base, materialization at
depths `0/1/5/20/100/200`, squash with and without native leases, and public
read compatibility after raw `/testbed` mutation. The follow-up public API
conflict run also covers imported-base `write_file`, `edit_file`, and `shell`
conflict behavior on focused fixture paths.

It is not yet a full load-bearing sign-off for the public runtime. The remaining
coverage should target in-flight shell lease plus squash/GC behavior and mixed
public runtime load on top of an imported workspace base before expanding into
broader importer scale cases.

## Follow-Up Coverage Status

1. Public API conflict tests over imported base

   Covered by
   `layer_stack_overlay_occ/test_workspace_base_public_api_conflicts.py`.
   The run exercises the real workspace-base public path for two writes to the
   same existing base file, edit vs edit on the same file with disjoint and
   overlapping hunks, delete vs write on the same path through shell delete plus
   public write, shell full-file tracked conflicts, shell writes to a tracked
   file plus gitignored output, and raw `/testbed` mutation after import.

2. In-flight shell lease plus squash

   Add a public shell request that holds a leased snapshot while another public
   API call edits the active stack, compact/squash runs, and GC runs. The shell
   must continue to see its frozen snapshot view, while the active stack must
   expose the later committed state after the concurrent edit.

3. Mixed load-bearing public API profile

   Add a mixed imported-base profile at concurrency `1/5/10/20`. The profile
   should cover read-heavy traffic, write/edit-heavy traffic, shell-light traffic
   with shell still present, periodic compact, and final replay or inventory
   reconciliation. This is the highest-value production-confidence test because
   it exercises the real user-visible runtime instead of only base import and
   read compatibility.

4. Crash safety beyond controlled exceptions

   Add interruption cases that kill execution during layer write, after base
   layer rename but before manifest write, after manifest write but before
   `workspace.json`, and before daemon restart plus `ensure_workspace_base`.
   The invariant is that no caller observes success unless binding, manifest,
   and base layer are mutually consistent.

5. Importer scale and path edge cases

   Add larger fixtures for thousands of small files, 32-128 MiB binary files,
   executable bits or file modes if they become part of the contract, dangling
   symlinks, symlink-to-directory entries, paths with spaces/unicode/long names,
   newline paths if supported, and deeply nested empty directories.

6. Budgets and redlines

   Promote the current baselines into loose redlines: base import runtime p99,
   materialize p99 by depth, squash p99 by depth, and mixed public API p99 under
   concurrency `10` and `20`.

Recommended priority order: public API conflicts, in-flight shell lease plus
squash, mixed load profile, crash safety, then scale/path edge cases.
