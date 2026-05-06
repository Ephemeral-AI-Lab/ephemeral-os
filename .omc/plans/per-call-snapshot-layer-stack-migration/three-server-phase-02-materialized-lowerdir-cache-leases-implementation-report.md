# Phase 02 Implementation Report - Materialized Lowerdir Cache and Lease Pins

**Date:** 2026-05-07
**Plan:** `three-server-phase-02-materialized-lowerdir-cache-leases.md`
**Status:** implemented and unit-verified

## Summary

Implemented the layer-stack snapshot preparation surface for guarded shell
follow-up work:

- Added a materialized lowerdir cache keyed by active manifest version and a
  deterministic manifest root identity hash.
- Extended workspace leases so one lease release path pins both manifest layer
  refs and materialized lowerdirs.
- Added `LayerStackManager.prepare_workspace_snapshot()` returning lease id,
  manifest version, root hash, lowerdir, cache hit/miss, byte count, and
  timings.
- Added lowerdir cache metrics and surfaced them through layer metrics.
- Extended GC to preserve lowerdirs pinned by active leases and remove unpinned
  materialized lowerdirs.
- Added runtime handlers and op registrations for
  `api.prepare_workspace_snapshot` and `api.release_workspace_snapshot`.

## Files Changed

Core layer-stack:

- `backend/src/sandbox/layer_stack/snapshot_cache.py`
  - New `MaterializedSnapshotCache`, `MaterializedSnapshot`, and
    `manifest_root_hash()`.
  - Cache hit path reads metadata only and does not rematerialize the payload.
- `backend/src/sandbox/layer_stack/metrics.py`
  - New `LowerdirCacheMetrics` contract.
- `backend/src/sandbox/layer_stack/lease_registry.py`
  - Added `WorkspaceLease` fields for root hash, lowerdir, workspace ref, and
    expiry.
  - Added lowerdir refcounts and lowerdir pin/release behavior.
- `backend/src/sandbox/layer_stack/stack_manager.py`
  - Added `PrepareWorkspaceSnapshotResult`.
  - Added snapshot preparation, lowerdir cache metric accessors, pinned
    lowerdir enumeration, and GC lowerdir cleanup.
- `backend/src/sandbox/layer_stack/__init__.py`
  - Exported `PrepareWorkspaceSnapshotResult`.

Runtime/control:

- `backend/src/sandbox/runtime/layer_stack_server.py`
  - Added shared process-local `LayerStackManager` cache for layer-stack runtime
    handlers.
  - Added prepare/release workspace snapshot server methods.
- `backend/src/sandbox/runtime/layer_stack_handlers.py`
  - Added `prepare_workspace_snapshot` and `release_workspace_snapshot`.
- `backend/src/sandbox/runtime/server.py`
  - Registered `api.prepare_workspace_snapshot` and
    `api.release_workspace_snapshot`.
- `backend/src/sandbox/runtime/api_handlers.py`
  - Reused the shared manager cache and exposed lowerdir cache metrics.
- `backend/src/sandbox/control/ops/runtime_services.py`
  - Added host-side runtime service helpers for prepare/release snapshot ops.

Tests:

- `backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py`
- `backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py`

## Exit Criteria

| Criterion | Result |
|---|---|
| Two preparations for the same manifest reuse one lowerdir | Covered by `test_prepare_workspace_snapshot_reuses_lowerdir_and_pins_until_release`. |
| Release of one lease does not unpin a lowerdir still used by another lease | Covered by both new tests. |
| GC keeps leased manifests and materialized lowerdirs | Covered by `test_prepare_workspace_snapshot_reuses_lowerdir_and_pins_until_release`. |
| Metrics distinguish cache hit, miss, bytes, and duration | Covered by cache metrics assertions and `LowerdirCacheMetrics`. |
| Cache-hit preparation does not walk/rematerialize the full workspace payload | Covered by `test_cache_hit_does_not_rematerialize_payload`. |

## Verification

Commands run:

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py -q
```

Result: `4 passed, 1 warning`.

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack -q
```

Result: `36 passed, 1 warning`.

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_runtime/test_daemon_backend.py backend/tests/unit_test/test_sandbox/test_api/test_shell_staleness_telemetry.py -q
```

Result: `14 passed, 1 warning`.

```bash
uv run ruff check backend/src/sandbox/layer_stack/metrics.py backend/src/sandbox/layer_stack/snapshot_cache.py backend/src/sandbox/layer_stack/lease_registry.py backend/src/sandbox/layer_stack/stack_manager.py backend/src/sandbox/runtime/layer_stack_server.py backend/src/sandbox/runtime/layer_stack_handlers.py backend/src/sandbox/runtime/api_handlers.py backend/src/sandbox/runtime/server.py backend/src/sandbox/control/ops/runtime_services.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py
```

Result: `All checks passed`.

```bash
uv run python -c "from sandbox.runtime.server import OP_TABLE; assert 'api.prepare_workspace_snapshot' in OP_TABLE; assert 'api.release_workspace_snapshot' in OP_TABLE"
```

Result: passed.

```bash
git diff --check -- backend/src/sandbox/control/ops/runtime_services.py backend/src/sandbox/layer_stack/__init__.py backend/src/sandbox/layer_stack/lease_registry.py backend/src/sandbox/layer_stack/stack_manager.py backend/src/sandbox/runtime/api_handlers.py backend/src/sandbox/runtime/layer_stack_handlers.py backend/src/sandbox/runtime/layer_stack_server.py backend/src/sandbox/runtime/server.py backend/src/sandbox/layer_stack/metrics.py backend/src/sandbox/layer_stack/snapshot_cache.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py
```

Result: passed.

## Notes

- This phase does not change the guarded shell mount namespace yet. Existing
  shell execution still uses the current runtime invoker path.
- The root hash is currently a deterministic identity hash over the manifest's
  layer refs. It is suitable for the Phase 02 cache key without requiring a
  full tree walk before cache lookup.
- The active worktree also contains a pre-existing modification to
  `backend/tests/live_e2e_test/sandbox/phase-01-workspace-base-report.md`; this
  report does not rely on or modify that artifact.
