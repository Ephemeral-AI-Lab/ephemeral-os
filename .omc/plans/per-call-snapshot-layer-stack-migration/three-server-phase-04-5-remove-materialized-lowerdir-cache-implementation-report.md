# Phase 04.5 Implementation Report — Remove Materialized Lowerdir Cache

**Date:** 2026-05-07
**Plan:** `three-server-phase-04-5-remove-materialized-lowerdir-cache.md`
**Status:** implemented and unit-verified (committed); follow-up cleanups in flight

## Summary

Phase 04.5 retired the persistent on-disk materialized lowerdir cache and the
`cache_policy` switch introduced in Phase 02 / Phase 04. After the Phase 04
cache A/B chose `keep_cache_recommendation = false` at every measured
concurrency tier, the cache layer ceased to be a justified path. Every
`prepare_workspace_snapshot` call now takes a single branch: acquire a
workspace lease, then materialize a fresh transient lowerdir under
`storage_root/runtime/transient-lowerdirs/<request>-<uuid>/lower`. The
shell-call cleanup path unconditionally deletes that lowerdir on lease
release; no `materialized/` directory is ever created by new code.

A best-effort `rmtree` of any legacy `materialized/` directory runs in
`LayerStackManager.__init__`, so existing layer-stack roots upgrade in place
without an explicit migration step.

The phase also opportunistically picked up two follow-up simplifications and
two Phase 03 narrow-protocol cleanups that surfaced once the cache surface was
gone (see “Follow-up Cleanups” below).

## Commits Landed

| SHA | Subject | Phase |
|---|---|---|
| `6fb0b1e0` | Phase 04.5 — remove materialized lowerdir cache | 04.5 main removal |
| `e947e1ab` | Phase 04.5 — drop dead lease fields and walk | 04.5 follow-up |
| `ab21cbef` | Phase 04.5 — drop dead owner_request_id fallback | 04.5 follow-up |
| `44adfbc1` | Phase 03 — drop port adapter and Protocol/Client duplicates | seam cleanup |
| `bed121f8` | Phase 03 — drop dead OccService attribute and OCCClient binding default | seam cleanup |

Net diff for Phase 04.5 (`6fb0b1e0`): **+521 / −2,248 LOC** across 23 files.

## Files Changed

Removed entirely:

- `backend/src/sandbox/layer_stack/snapshot_cache.py` — the
  `MaterializedSnapshotCache`, `MaterializedSnapshot`, and `manifest_root_hash`
  helpers. `manifest_root_hash` moved into `sandbox.layer_stack.manifest`.
- `backend/src/sandbox/layer_stack/metrics.py` — the `LowerdirCacheMetrics`
  contract.
- `backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py`
- `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_replaced_shell_cache_ab.py`
- `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_snapshot_cache_leases.py`
- `backend/tests/live_e2e_test/sandbox/_harness/snapshot_cache_metrics.py`

Edited:

- `backend/src/sandbox/layer_stack/stack_manager.py`
  - `PrepareWorkspaceSnapshotResult` reduced to
    `{lease_id, manifest_version, root_hash, manifest, lowerdir, timings}`.
    Removed `cache_hit`, `cache_policy`, `transient_lowerdir`, and (in the
    `e947e1ab` follow-up) `materialized_byte_count` plus the `_byte_count`
    rglob walk that re-scanned the lowerdir after materialization in the shell
    hot path.
  - `prepare_workspace_snapshot` collapsed to `acquire lease →
    materialize_transient_lowerdir → return result`. The cache lookup, cache
    pinning, and cache eviction branches are gone.
  - `release_lease` no longer evicts cache entries; lowerdir cleanup is the
    caller's responsibility (`command_exec_server` deletes the lowerdir parent
    after release).
  - `__init__` does a best-effort `rmtree(storage_root / "materialized")` if
    the legacy directory exists.
  - Dropped `lowerdir_cache_metrics()` and `materialized_lowerdir_count()`.

- `backend/src/sandbox/layer_stack/lease_registry.py`
  - `WorkspaceLease.materialized_lowerdir` removed.
  - `LeaseRegistry.pin_lowerdir`, `pinned_lowerdirs`, and the
    `_lowerdir_refcounts` map removed; `acquire()` no longer takes a
    `materialized_lowerdir` keyword.

- `backend/src/sandbox/layer_stack/manifest.py`
  - Took ownership of `manifest_root_hash()` (previously imported from
    `snapshot_cache`).

- `backend/src/sandbox/runtime/clients/layer_stack.py`
  - `LayerStackClient.prepare_workspace_snapshot` lost the `cache_policy` and
    `ttl_seconds` parameters; the previous `_prepare_transient_workspace_snapshot`
    helper was promoted into the only path. The `WorkspaceLeaseClient`
    Protocol matches.

- `backend/src/sandbox/command_exec/clients.py`
  - `WorkspaceLeaseClient` Protocol dropped `cache_policy` and `ttl_seconds`.
  - `WorkspaceSnapshotLease` dropped `cache_hit` and `materialized_byte_count`.

- `backend/src/sandbox/runtime/command_exec_server.py`
  - Removed `_snapshot_cache_policy` and the
    `EPHEMERALOS_COMMAND_EXEC_SNAPSHOT_CACHE_POLICY` env var read.
  - `_drop_transient_lowerdir` is unconditional now; the `transient_lowerdir`
    guard is gone.

- `backend/src/sandbox/runtime/api_handlers.py`
  - Stopped exposing `lowerdir_cache_metrics`.

- `backend/src/sandbox/runtime/layer_stack_handlers.py`
  - `prepare_workspace_snapshot` response shape no longer carries
    `cache_hit` / `cache_policy` / `transient_lowerdir`.
  - `_owner_request_id` lost its dead `owner_request_id` fallback (only
    `request_id` is ever passed by callers).

- `backend/src/sandbox/occ/ports.py`
  - `OccLayerStackPorts.snapshot_cache_root` renamed to
    `gitignore_cache_root`. The on-disk path moved from
    `storage_root/materialized/` to `storage_root/runtime/gitignore-cache/`.

- `backend/src/sandbox/occ/content/gitignore_oracle.py`
  - `_ensure_disk_cached_workspace` now reads `materializer.gitignore_cache_root`.
    Per-version subdir layout unchanged.

- `backend/src/sandbox/control/ops/runtime_services.py`
  - Public-API tool layer no longer threads `cache_policy`.

Tests updated:

- `backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py`
  — drop `pinned_lowerdirs` / `pin_lowerdir` assertions; verify two leases on
  one manifest hold distinct lowerdirs.
- `backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_lease.py`
  — verify no cache reuse, transient lowerdir cleanup, and missing-binding
  failure.
- `backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py`
  — drop `ttl_seconds`, `cache_policy`, and `cache_hit` assertions.
- `backend/tests/unit_test/test_sandbox/test_api/test_gitignore_oracle_cache.py`
  — assert against the renamed `gitignore_cache_root` path.

## Runtime Workflow (after removal)

```text
host sandbox.api.tool.shell(...)
  -> runtime op api.shell
  -> command_exec_server.shell
  -> LayerStackClient.prepare_workspace_snapshot(request_id)
       -> manager.acquire_snapshot_lease(request_id)
       -> manager.materialize(transient_lowerdir, lease.manifest)
       -> return PrepareWorkspaceSnapshotResult{lowerdir=transient_lowerdir}
  -> workspace replacement mount + run + capture
  -> OCCClient.apply_changeset(...)
  -> manager.release_lease(lease_id)
  -> rmtree(transient_lowerdir.parent)
```

```text
storage_root/
  layers/                 # unchanged
  staging/                # unchanged
  manifest.json           # unchanged
  runtime/
    transient-lowerdirs/  # one dir per active shell call, deleted on release
    gitignore-cache/      # was snapshot_cache_root, renamed and relocated
  materialized/           # never created by new code; legacy dirs rmtree'd at startup
```

## Exit Criteria

| Criterion | Result |
|---|---|
| Two leases on the same manifest receive distinct lowerdir paths | `test_snapshot_lease.py` (assertion added) |
| Second prepare for the same manifest still walks the merged view (no cache hit) | `test_snapshot_lease.py` |
| Release + cleanup deletes the transient lowerdir parent | Verified by `pathlib.Path.exists()` assertion in `test_snapshot_lease.py` |
| `import sandbox.layer_stack.snapshot_cache` raises `ModuleNotFoundError` | Verified via runtime probe and `test_import_fence.py` allow-list |
| `import sandbox.layer_stack.metrics` raises `ModuleNotFoundError` | Verified via runtime probe and `test_import_fence.py` |
| `OccLayerStackPorts` no longer exposes `snapshot_cache_root` | `test_occ` suite green after rename |
| Gitignore oracle still passes after path rename | `test_gitignore_oracle_cache.py` green |
| `command_exec_server` ignores `EPHEMERALOS_COMMAND_EXEC_SNAPSHOT_CACHE_POLICY` | Env var read deleted |
| Runtime `OP_TABLE` does not register `api.compact` or any cache metric op | Probe: `'api.compact' not in OP_TABLE`; `prepare`/`release` still registered |
| Fresh layer-stack root has no `materialized/` after a full shell + release cycle | `LayerStackManager.__init__` rmtree + cleanup never recreates it |

## Verification

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack \
              backend/tests/unit_test/test_sandbox/test_command_exec \
              backend/tests/unit_test/test_sandbox/test_api/test_shell.py \
              backend/tests/unit_test/test_sandbox/test_runtime \
              backend/tests/unit_test/test_sandbox/test_import_fence.py \
              backend/tests/unit_test/test_sandbox/test_occ -q
```

At the Phase 04.5 commit boundary (`ab21cbef`): all targeted suites green.

Module-fence probe:

```bash
uv run python -c "
import sandbox.layer_stack.snapshot_cache  # expect ModuleNotFoundError
"
uv run python -c "
import sandbox.layer_stack.metrics          # expect ModuleNotFoundError
"
```

Both raise `ModuleNotFoundError` as required.

OP_TABLE probe:

```bash
uv run python -c "
from sandbox.runtime.server import OP_TABLE
assert 'api.compact' not in OP_TABLE
assert 'api.prepare_workspace_snapshot' in OP_TABLE
assert 'api.release_workspace_snapshot' in OP_TABLE
assert 'api.shell' in OP_TABLE
"
```

Result: passed.

Live wall-clock parity (per plan §6) was not re-run as a separate gate; the
removed code path is byte-for-byte the `cache_disabled` arm that already
shipped through the Phase 04 A/B (`.omc/results/live-e2e-phase04-shell-cache-ab-summary-20260506T190942Z.jsonl`).

## Follow-up Cleanups (committed and in flight)

### Committed alongside 04.5 (Phase 03 narrow protocols)

- `44adfbc1` — Removed the 78-LOC `_LayerStackPortsAdapter` that renamed
  manager methods (`read_active_manifest` → `get_active_manifest`,
  `materialize` → `materialize_snapshot`) and faked staging / gitignore-cache
  helpers. With staging now lifted onto `LayerStackManager` and the Protocol
  method names aligned to the manager, the adapter,
  `ensure_layer_stack_ports`, `_has_narrow_ports`, and the OCCClient re-export
  in `runtime/clients/occ.py` all became dead. `LayerStackClient` now
  forwards directly to the manager. Net **−102 LOC** across the seam; fence
  tests still pass.
- `bed121f8` — Dropped `OccService._workspace_ref` (set, never read after
  the prior commit removed the only reader) and removed the
  `binding_reader=None` default on `OCCClient` (every real caller —
  `command_exec_server` and the OCC ports test — supplies one).

### In flight (uncommitted at the time of this report)

The working tree contains a follow-on simplification batch that builds on the
04.5 / 03 cleanup:

- Removed `api.shell_batch` (and `sandbox.api.tool.shell.shell_batch`,
  `command_exec_server.shell_batch`, `_payload_results`, `_batch_timeout`,
  `_require_result`). Single-shot `api.shell` is the only public shell op now.
- Trimmed `runtime/api_handlers.py` (−246 LOC) by removing the legacy
  `_shell_with_services` snapshot-overlay path that was already shadowed by
  `command_exec_server.shell`.
- Switched `_apply_workspace_capture` to `atomic = len(distinct_paths) > 1`
  so single-path captures opt out of cross-path atomicity and let
  `OccSerialMerger._disjoint_batches` coalesce them with concurrent disjoint
  commits. Multi-path captures still pin `atomic=True`.
- Simplified `workspace_base.py` and `runtime/layer_stack_handlers.py`
  responses; trimmed `OccService` constructor (`workspace_ref` removed).
- Added `backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_changeset.py`
  for the workspace-changes → OCC-changes converter.

Status of the in-flight batch:

- `144 passed, 1 failed` in the targeted suites.
- The single failure is `test_capture_to_occ_client.py::test_shell_capture_goes_through_occ_client_before_lease_release`,
  which still asserts `occ.atomic is True` after the
  `atomic-by-path-count` change. This is a stale assertion to update before
  committing — the production code is doing the right thing.

## Notes and Open Items

- The root hash key under the old cache used a deterministic identity hash
  over manifest layer refs. With the cache gone, the hash now serves only as
  an OCC `snapshot` identifier and a `PrepareWorkspaceSnapshotResult` field
  for callers that need to compare snapshots. Nothing keys it for storage
  anymore.
- The shell hot path picked up a small saving from removing the post-
  materialize `Path.rglob` walk that produced `materialized_byte_count`;
  callers never consumed the field.
- The Phase 02 plan was annotated to mark the cache as removed; the phase
  index reflects the same.
- Rollback path: a single `git revert 6fb0b1e0` restores Phase 02's cache and
  Phase 04's `cache_policy` switch byte-for-byte. No data migration is
  needed because new layer-stack roots never write to `materialized/`, and
  legacy roots are rmtree'd on first manager init.
- Before declaring the in-flight batch landed, fix the stale `atomic is True`
  assertion in `test_capture_to_occ_client.py`, then re-run the targeted
  pytest invocation above plus `ruff check backend/src/sandbox backend/tests`
  and the live `test_shell_call_isolation.py` smoke gate.
