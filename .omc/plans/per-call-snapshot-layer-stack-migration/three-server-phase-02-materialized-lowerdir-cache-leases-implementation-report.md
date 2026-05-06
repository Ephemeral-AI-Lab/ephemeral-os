# Phase 02 Implementation Report - Materialized Lowerdir Cache and Lease Pins

**Date:** 2026-05-07
**Plan:** `three-server-phase-02-materialized-lowerdir-cache-leases.md`
**Status:** implemented, unit-verified, and live-verified

## Summary

Implemented the layer-stack snapshot preparation surface for guarded shell
follow-up work:

- Added a materialized lowerdir cache keyed by active manifest version and a
  deterministic manifest root identity hash.
- Extended workspace leases so one lease release path pins both manifest layer
  refs and materialized lowerdirs.
- Updated cache eviction so the lease release path is the only lowerdir cache
  eviction authority. A materialized lowerdir is removed only when the released
  lease is the last lease pinning it and that lease's manifest is no longer the
  active manifest.
- Removed the public `api.compact` operation and layer-stack sweep API. Stale
  layer directories are now removed by the same event-driven lease/squash
  policy: immediately during squash when unleased, or when the final stale lease
  releases.
- Added `LayerStackManager.prepare_workspace_snapshot()` returning lease id,
  manifest version, root hash, lowerdir, cache hit/miss, byte count, and
  timings.
- Added lowerdir cache metrics and surfaced them through layer metrics.
- Added runtime handlers and op registrations for
  `api.prepare_workspace_snapshot` and `api.release_workspace_snapshot`.
- Added Daytona-backed live E2E coverage for the public runtime
  prepare/release/metrics surface, including JSONL artifacts for cache policy,
  disk-size, and cold-miss versus warm-hit performance evidence.

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
    lowerdir enumeration, and release-time stale lowerdir eviction.
  - Removed the sweep-style cleanup API and moved stale layer deletion into
    squash/release events.
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
  - Removed `api.compact` registration.
- `backend/src/sandbox/runtime/api_handlers.py`
  - Reused the shared manager cache and exposed lowerdir cache metrics.
- `backend/src/sandbox/control/ops/runtime_services.py`
  - Added host-side runtime service helpers for prepare/release snapshot ops.

Tests:

- `backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py`
- `backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py`
- `backend/tests/live_e2e_test/sandbox/_harness/snapshot_cache_metrics.py`
- `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_snapshot_cache_leases.py`

## Exit Criteria

| Criterion | Result |
|---|---|
| Two preparations for the same manifest reuse one lowerdir | Covered by `test_prepare_workspace_snapshot_reuses_latest_lowerdir_until_release_observes_stale`. |
| Release of one lease does not unpin a lowerdir still used by another lease | Covered by both new tests. |
| Latest materialized lowerdir remains reusable after leases drain | Covered by `test_prepare_workspace_snapshot_reuses_latest_lowerdir_until_release_observes_stale`. |
| Manifest advancement alone does not evict lowerdir cache | Covered by `test_prepare_workspace_snapshot_reuses_latest_lowerdir_until_release_observes_stale`. |
| Final stale lease release deletes the materialized lowerdir | Covered by `test_stale_lowerdir_is_removed_when_final_lease_releases`. |
| Metrics distinguish cache hit, miss, bytes, and duration | Covered by cache metrics assertions and `LowerdirCacheMetrics`. |
| Cache-hit preparation does not walk/rematerialize the full workspace payload | Covered by `test_cache_hit_does_not_rematerialize_payload`. |
| Public runtime prepare/release works in a real Daytona sandbox | Covered by `test_workspace_snapshot_cache_leases.py` live run. |
| Concurrent same-manifest prepare fans into one lowerdir | Covered by `test_concurrent_prepare_same_manifest_fans_into_one_lowerdir`. |
| Deep manifests create one cache entry per manifest identity, not one per layer | Covered by `test_deep_manifest_materializes_one_cache_entry_not_one_per_layer`. |
| Cache hits are faster than cold materialization in the same sandbox run | Covered by `test_cache_hit_reduces_prepare_cost_for_same_manifest`. |
| Cache byte size and eviction are observable on the sandbox volume | Covered by `test_cache_size_is_observable_and_eviction_returns_unpinned_space`. |
| Missing workspace binding fails closed without cache or lease residue | Covered by `test_prepare_workspace_snapshot_fails_closed_without_workspace_binding`. |

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

Follow-up verification after public compact and sweep removal:

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack -q
```

Result: `35 passed, 1 warning`.

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_runtime -q
```

Result: `46 passed, 1 warning`.

```bash
uv run pytest backend/tests/unit_test/test_sandbox -q
```

Result: `349 passed, 1 skipped, 1 warning`.

```bash
uv run pytest backend/tests/live_e2e_test/sandbox --collect-only -q
```

Result: `97 tests collected, 1 warning`.

```bash
uv run python -c "from sandbox.runtime.server import OP_TABLE; assert 'api.compact' not in OP_TABLE; assert 'api.prepare_workspace_snapshot' in OP_TABLE; assert 'api.release_workspace_snapshot' in OP_TABLE"
```

Result: passed.

```bash
uv run ruff check backend/src/sandbox backend/tests/unit_test/test_sandbox backend/tests/live_e2e_test/sandbox
```

Result: `All checks passed`.

Release-only cache eviction verification:

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py -q
```

Result: `6 passed, 1 warning`.

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack -q
```

Result: `34 passed, 1 warning`.

```bash
uv run ruff check backend/src/sandbox/layer_stack/stack_manager.py backend/src/sandbox/layer_stack/snapshot_cache.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_snapshot_cache_leases.py backend/tests/live_e2e_test/sandbox/_harness/snapshot_cache_metrics.py
```

Result: `All checks passed`.

```bash
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_snapshot_cache_leases.py --collect-only -q
```

Result: `8 tests collected, 1 warning`.

```bash
uv run python -c "from sandbox.runtime.server import OP_TABLE; assert 'api.compact' not in OP_TABLE; assert 'api.prepare_workspace_snapshot' in OP_TABLE; assert 'api.release_workspace_snapshot' in OP_TABLE"
```

Result: passed.

```bash
git diff --check
```

Result: passed.

Live E2E verification after release-only cache eviction:

```bash
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_snapshot_cache_leases.py -q
```

Result: `8 passed, 1 warning in 95.84s`.

Latest Phase 02 JSONL artifacts were written under `.omc/results/` with the
`sandbox.live_e2e.phase02_snapshot_cache_leases.v1` schema. The latest reuse
artifact recorded `materialized_lowerdirs=1` after manifest advancement and the
`manifest_advance_did_not_evict_stale_unleased_lowerdir` pass bar. The
performance artifact recorded 5 paired samples at 16 MiB, median cold miss
`425.401 ms`, median warm hit `404.164 ms`, and median materialization avoided
`35.179 ms`. The disk artifact recorded materialized cache bytes increasing
from `0` to `36,216,786` after prepare, remaining pinned after manifest
advancement while the lease was active, and returning to `8,192` after final
stale lease release.

## Notes

- This phase does not change the guarded shell mount namespace yet. Existing
  shell execution still uses the current runtime invoker path.
- The root hash is currently a deterministic identity hash over the manifest's
  layer refs. It is suitable for the Phase 02 cache key without requiring a
  full tree walk before cache lookup.
- The active worktree also contains a pre-existing modification to
  `backend/tests/live_e2e_test/sandbox/phase-01-workspace-base-report.md`; this
  report does not rely on or modify that artifact.
