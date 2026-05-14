# OCC Architecture Remediation Implementation Report

Source review: `.planning/code-reviews/occ-architecture-review.md`

## Phase 1 — Change Dispatch And Staging Contracts

Status: complete

Changes:

- Added `sandbox.occ.stage.policy.MergePolicy` plus shared staging callable
  aliases.
- Reworked `WriteChange` around eager and disk-backed payload objects while
  preserving existing constructor call sites.
- Added cached reads for disk-backed write payloads.
- Normalized `OpaqueDirChange.kept_children` so only direct child names are
  accepted.
- Replaced direct/gated `isinstance(change, ...)` cascades with handler tables.
- Wired `CommitTransaction` through a `RouteDecision -> MergePolicy` map.

Verification:

- `uv run pytest backend/tests/unit_test/test_sandbox/test_occ/test_changeset_builders.py backend/tests/unit_test/test_sandbox/test_occ/test_direct_merge.py backend/tests/unit_test/test_sandbox/test_occ/test_tracked_merge.py backend/tests/unit_test/test_sandbox/test_occ/test_base_hash_inference.py -q`
- `python3 -m compileall -q backend/src/sandbox/occ`
- `rg -n "isinstance\\(change" backend/src/sandbox/occ/merge backend/src/sandbox/occ/commit_transaction.py`

Result:

- 16 tests passed.
- OCC package compiled.
- No remaining `isinstance(change, ...)` usage in the stagers or commit
  transaction.

## Phase 2 — Routing And Hashing Consolidation

Status: complete

Changes:

- Added canonical route names: `RouteDecision.GATED` and
  `RouteDecision.DIRECT`.
- Reworked the old orchestrator into a canonical `Router` implementation.
- Folded single-path preparation into `Router.prepare_single_path_sync` and
  removed the duplicate `routing/single_path.py` module.
- Deleted `routing/runtime_ops.py` and moved hash helpers into
  `sandbox.occ.content.hashing`.
- Added explicit `SnapshotGitignoreMatcher` and `GitignoreCacheStats`
  protocols.
- Replaced snapshot gitignore `getattr` probing with a fail-closed protocol
  check.
- Updated unit-test gitignore fakes that route against snapshots to implement
  `is_ignored_in_snapshot`.

Verification:

- `uv run pytest backend/tests/unit_test/test_sandbox/test_occ/test_changeset_routing.py backend/tests/unit_test/test_sandbox/test_occ/test_base_hash_inference.py backend/tests/unit_test/test_sandbox/test_occ/test_commit_transaction.py backend/tests/unit_test/test_sandbox/test_occ/test_gitignore_policy_edge_cases.py -q`
- `python3 -m compileall -q backend/src/sandbox/occ`
- `test ! -e backend/src/sandbox/occ/routing/runtime_ops.py`
- `rg -n "getattr\\(oracle|is_ignored_in_snapshot" backend/src/sandbox/occ/routing backend/src/sandbox/occ/content/gitignore_oracle.py`

Result:

- 18 tests passed.
- OCC package compiled.
- `runtime_ops.py` is removed.
- Routing no longer probes `is_ignored_in_snapshot` with `getattr`; snapshot
  routing requires the explicit snapshot-aware protocol.

## Phase 3 — Service, Ports, Maintenance, And Queue Lifecycle

Status: complete

Changes:

- Removed the welded `OccLayerStackPorts` protocol from the port surface.
- Renamed the storage transaction protocol to `CommitTransactionPort`, keeping a
  temporary compatibility alias for older imports.
- Changed `CommitTransaction` to require explicit snapshot/staging/publisher
  ports.
- Promoted the staging seam to `LayerChangeStager` with
  `FileSystemLayerChangeStager` as the concrete implementation.
- Extracted auto-squash into `AutoSquashMaintenancePolicy` /
  `NoopMaintenancePolicy`.
- Added `RetryPolicy` for serial CAS retry limits.
- Changed `CommitQueue` so the worker thread starts through `start()` and
  stops through `close()`.
- Added `TimingKey` as the stable registry for OCC timing metric names and
  moved OCC timing emissions to enum keys.

Verification:

- `uv run pytest backend/tests/unit_test/test_sandbox/test_occ/test_changeset_builders.py backend/tests/unit_test/test_sandbox/test_occ/test_direct_merge.py backend/tests/unit_test/test_sandbox/test_occ/test_tracked_merge.py backend/tests/unit_test/test_sandbox/test_occ/test_base_hash_inference.py backend/tests/unit_test/test_sandbox/test_occ/test_commit_transaction.py backend/tests/unit_test/test_sandbox/test_occ/test_concurrent_commits.py backend/tests/unit_test/test_sandbox/test_occ/test_gitignore_policy_edge_cases.py backend/tests/unit_test/test_sandbox/test_occ/test_auto_squash.py -q`
- `python3 -m compileall -q backend/src/sandbox/occ`
- `rg -no '"(occ|layer_stack|gitignore)\\.[^"]+"|"_occ\\.[^"]+"' backend/src/sandbox/occ`

Result:

- 32 tests passed.
- OCC package compiled.
- The only remaining raw OCC timing strings are the enum values in
  `timing_keys.py`.

## Phase 4 — Naming, Structure, And Consumer Ownership

Status: complete

Changes:

- Removed the remaining prefixed service/client/class names from Python code:
  production and tests now use `Service`, `Client`, `CommitQueue`,
  `CommitTransaction`, `DirectStager`, `GatedStager`, and `Router`.
- Removed legacy module paths from Python code: staging lives under
  `sandbox.occ.stage`, routing in `sandbox.occ.router`, overlay capture in
  `sandbox.occ.overlay`, and result projection in
  `sandbox.runtime.daemon.service.result_projection`.
- Made `Service` require explicit `snapshot_reader`, `staging`, and `publisher`
  ports at construction. Runtime backend wiring passes auto-squash as an
  explicit `AutoSquashMaintenancePolicy`.
- Replaced the router base-hash `isinstance(change, ...)` chain with a
  change-behavior dispatch table.
- Updated daemon bundle/import-fence tests and OCC unit/live test imports for
  the canonical structure.

Verification:

- `python3 -m compileall -q backend/src/sandbox/occ backend/src/sandbox/runtime/daemon/service backend/src/sandbox/runtime/daemon/handler`
- `uv run pytest backend/tests/unit_test/test_sandbox/test_occ -q`
- `uv run pytest backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_api/test_guarded_result_status.py backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py backend/tests/unit_test/test_sandbox/test_occ/test_shell_capture_atomicity.py -q`
- `uv run pytest backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_import_fence.py backend/tests/unit_test/test_sandbox/test_api/test_guarded_result_status.py backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py backend/tests/unit_test/test_sandbox/test_occ/test_shell_capture_atomicity.py -q`
- `uv run ruff check backend/src/sandbox/occ backend/src/sandbox/runtime/daemon/service/occ_backend.py backend/src/sandbox/runtime/daemon/service/result_projection.py backend/tests/unit_test/test_sandbox/test_occ backend/tests/live_e2e_test/sandbox/occ backend/tests/unit_test/test_sandbox/test_daemon/test_daemon.py backend/tests/unit_test/test_sandbox/test_daemon/test_bundle_upload.py backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py`
- `rg -n "Service\\([^\\n]*layer_stack=|auto_squash_max_depth|\\bOccService\\b|\\bOCCClient\\b|\\bOccOrchestrator\\b|\\bOccSerialMerger\\b|\\bOccCommitTransaction\\b|\\bDirectMerge\\b|\\bGatedMerge\\b|sandbox\\.occ\\.(merge|commit_transaction|routing|capture|result_projection)|RouteDecision\\.(OCC_SKIPPED_MERGE|OCC_GATED_MERGE)|OCC_SKIPPED_MERGE|OCC_GATED_MERGE" backend/src backend/tests --glob "*.py" --glob "!**/__pycache__/**"`
- `uv run pytest backend/tests/unit_test/test_sandbox/test_api backend/tests/unit_test/test_sandbox/test_occ -q` -> 163 passed.
- `uv run pytest backend/tests/unit_test/test_sandbox/test_api backend/tests/unit_test/test_sandbox/test_host backend/tests/unit_test/test_sandbox/test_runtime_bootstrap.py backend/tests/unit_test/test_sandbox/test_live_setup_api.py backend/tests/unit_test/test_sandbox/test_occ backend/tests/unit_test/test_sandbox/test_command_exec/test_edit_snapshot_byte_derivation.py backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py -q` -> 190 passed, 1 skipped.
- `rg -n "isinstance\\(change," backend/src/sandbox/occ`

Result:

- 57 OCC unit tests passed.
- 28 daemon/bundle/client-boundary tests passed.
- The broader import-fence rerun is blocked by the current dirty sandbox API
  worktree: `test_removed_api_compatibility_modules_stay_absent` fails because
  `sandbox.api.status` is importable. The failure is outside the OCC package
  and does not involve the moved OCC module paths.
- Touched OCC/runtime/test files pass Ruff.
- OCC/runtime daemon modules compile.
- No remaining Python references to the old prefixed OCC names, old OCC module
  paths, double-negative route names, `Service(..., layer_stack=...)`, or
  router/stager `isinstance(change, ...)` dispatch.
