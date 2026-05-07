# Phase 04.5 - Remove Materialized Lowerdir Cache

**Status:** implemented
**Source:** `three-server-phase-04-workspace-replaced-shell-implementation-report.md`
**Decision basis:** Phase 04 cache A/B - `keep_cache_recommendation = false`
on every concurrency tier; cache-enabled was slower at 1, 5, 10 and below the
250 ms / 20% bar at 20.

## 1. Task Specification

Remove the persistent materialized lowerdir cache introduced in Phase 02 and the
cache-policy switch added in Phase 04. Make per-lease transient lowerdir
construction the only path through `prepare_workspace_snapshot`.

What stays:

```text
workspace-replaced shell from Phase 04
workspace lease registry (manifest layer pinning)
public api.prepare_workspace_snapshot / api.release_workspace_snapshot ops
OCC client boundary unchanged
gitignore oracle's per-version materialized git workspace (relocated, not
removed)
```

What is removed:

```text
sandbox.layer_stack.snapshot_cache.MaterializedSnapshotCache and module
sandbox.layer_stack.metrics.LowerdirCacheMetrics and module
LayerStackManager._snapshot_cache and lowerdir_cache_metrics()
LayerStackManager.materialized_lowerdir_count()
WorkspaceLease.materialized_lowerdir field
LeaseRegistry.pin_lowerdir / pinned_lowerdirs / _lowerdir_refcounts
PrepareWorkspaceSnapshotResult.cache_hit / cache_policy / transient_lowerdir
LayerStackClient cache_policy parameter and the dual-path branching
WorkspaceLeaseClient protocol cache_policy parameter
WorkspaceSnapshotLease.cache_hit / materialized_byte_count
command_exec_server._snapshot_cache_policy
EPHEMERALOS_COMMAND_EXEC_SNAPSHOT_CACHE_POLICY env variable
OccLayerStackPorts.snapshot_cache_root
backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py
backend/tests/live_e2e_test/.../test_workspace_replaced_shell_cache_ab.py
backend/tests/live_e2e_test/sandbox/_harness/snapshot_cache_metrics.py
storage_root/materialized/ on disk (best-effort cleanup, see §9)
```

Out of scope:

```text
no change to workspace replacement mount, upperdir capture, or OCC submission
no change to squash, GC, or lease layer pinning
no change to the public OP_TABLE or tool surface
no replacement cache - the design is no cache, not a different cache
```

Exit condition:

```text
sandbox.api.tool.shell still routes through command_exec_server, but
LayerStackClient.prepare_workspace_snapshot has a single branch that
acquires a lease and materializes a fresh transient lowerdir under
storage_root/runtime/transient-lowerdirs/<request>-<uuid>/lower.
release_lease drops lease bookkeeping and command_exec_server unconditionally
deletes the transient lowerdir. The /materialized directory is never created
by new code, sandbox.layer_stack.snapshot_cache and sandbox.layer_stack.metrics
are not importable, and live shell wall-clock matches the cache_disabled column
of the Phase 04 A/B summary.
```

## 2. Main Data Objects (after removal)

```text
WorkspaceLease
  lease_id
  manifest
  owner_request_id
  acquired_at

PrepareWorkspaceSnapshotResult
  lease_id
  manifest_version
  root_hash
  manifest
  lowerdir            # transient, owned by caller, deleted on release
  materialized_byte_count
  timings

WorkspaceSnapshotLease (protocol used by command-exec)
  lease_id
  manifest_version
  root_hash
  manifest
  lowerdir
  timings
```

Removed fields: `cache_hit`, `cache_policy`, `transient_lowerdir`,
`materialized_lowerdir`.

## 3. File/Folder Structure Change

Deletions:

```text
backend/src/sandbox/layer_stack/snapshot_cache.py
backend/src/sandbox/layer_stack/metrics.py
backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py
backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/
    test_workspace_replaced_shell_cache_ab.py
backend/tests/live_e2e_test/sandbox/_harness/snapshot_cache_metrics.py
```

Edits:

```text
backend/src/sandbox/layer_stack/stack_manager.py
  drop _snapshot_cache, manifest_root_hash import path replaced with a local
  hash helper or inlined
  drop lowerdir_cache_metrics() and materialized_lowerdir_count()
  prepare_workspace_snapshot becomes: acquire lease then materialize a
  per-request transient lowerdir
  release_lease: drop the cache eviction branch, lowerdir cleanup is the
  caller's responsibility
  PrepareWorkspaceSnapshotResult: drop cache_hit / cache_policy /
  transient_lowerdir
  __init__: best-effort rmtree(storage_root / "materialized") if it exists

backend/src/sandbox/layer_stack/lease_registry.py
  WorkspaceLease: drop materialized_lowerdir
  LeaseRegistry: drop pin_lowerdir, pinned_lowerdirs, _lowerdir_refcounts and
  the materialized_lowerdir keyword on acquire()

backend/src/sandbox/runtime/clients/layer_stack.py
  prepare_workspace_snapshot: drop cache_policy parameter; the only path is the
  current _prepare_transient_workspace_snapshot logic, promoted to the body
  delete _prepare_transient_workspace_snapshot helper after promotion

backend/src/sandbox/command_exec/clients.py
  WorkspaceLeaseClient: drop cache_policy keyword
  WorkspaceSnapshotLease: drop cache_hit and materialized_byte_count

backend/src/sandbox/runtime/command_exec_server.py
  drop _snapshot_cache_policy and EPHEMERALOS_COMMAND_EXEC_SNAPSHOT_CACHE_POLICY
  drop cache_policy keyword on prepare_workspace_snapshot call
  _drop_transient_lowerdir: keep, but remove the transient_lowerdir guard;
  always delete the lowerdir parent on release

backend/src/sandbox/occ/ports.py
  remove snapshot_cache_root from OccLayerStackPorts and the adapter
  add gitignore_cache_root (or similar dedicated name) pointing to
  storage_root / "runtime" / "gitignore-cache"

backend/src/sandbox/occ/content/gitignore_oracle.py
  switch _ensure_disk_cached_workspace to materializer.gitignore_cache_root
  (rename only; the per-version subdir layout stays unchanged)

backend/src/sandbox/runtime/layer_stack_server.py
backend/src/sandbox/runtime/layer_stack_handlers.py
  prepare_workspace_snapshot signature: drop cache_policy passthrough
  response payload: drop cache_hit / cache_policy / transient_lowerdir keys

backend/src/sandbox/control/ops/runtime_services.py
  drop cache_policy on the public-API tool layer

backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py
backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_lease.py
backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py
backend/tests/unit_test/test_sandbox/test_runtime/test_daemon.py
backend/tests/unit_test/test_sandbox/test_runtime/test_daemon_backend.py
  drop cache-policy / cache-hit / pinned_lowerdirs assertions
  add: two leases on the same manifest get distinct lowerdirs
  add: release_lease followed by command_exec cleanup leaves no transient
  lowerdir on disk
```

## 4. Workflow Demonstration

```text
host sandbox.api.tool.shell(...)
  -> runtime op api.shell
  -> command_exec_server.shell
  -> LayerStackClient.prepare_workspace_snapshot(request_id)
       -> manager.acquire_snapshot_lease(request_id)
       -> manager.materialize(transient_lowerdir, lease.manifest)
       -> return PrepareWorkspaceSnapshotResult{lowerdir=transient_lowerdir}
  -> workspace replacement mount + run + capture + OCC apply
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
  materialized/           # NOT created by new code
```

## 5. Naming Conventions and Rationale

| Old | New | Rationale |
|---|---|---|
| `MaterializedSnapshotCache` | (removed) | no cache to name |
| `cache_policy=enabled\|disabled` | (removed) | only transient construction remains |
| `transient_lowerdir: bool` flag on result | (removed) | implicit; every lowerdir is transient |
| `LowerdirCacheMetrics` | (removed) | no cache to measure |
| `OccLayerStackPorts.snapshot_cache_root` | `gitignore_cache_root` | the only consumer is the gitignore oracle; name should reflect that |
| `prepare_workspace_snapshot` | unchanged externally | keeps `api.prepare_workspace_snapshot` op stable |

## 6. Tests and Exit Criteria

Unit suite:

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack -q
uv run pytest backend/tests/unit_test/test_sandbox/test_command_exec -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api/test_shell.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_runtime -q
uv run pytest backend/tests/unit_test/test_sandbox/test_import_fence.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_occ -q
```

Static and lint:

```bash
uv run ruff check backend/src/sandbox backend/tests
uv run mypy --config-file backend/mypy.ini backend/src/sandbox
```

Required assertions:

- two leases on the same manifest receive distinct lowerdir paths
- second prepare for the same manifest still walks the merged view (no cache
  hit because there is no cache)
- release_lease followed by command_exec cleanup deletes the transient lowerdir
  parent, observable via `pathlib.Path.exists()`
- runtime `OP_TABLE` does not register `api.compact` or any cache metric op
- `import sandbox.layer_stack.snapshot_cache` raises `ModuleNotFoundError`
  (covered by `test_import_fence.py` allow-list update)
- `import sandbox.layer_stack.metrics` raises `ModuleNotFoundError`
- `OccLayerStackPorts` no longer exposes `snapshot_cache_root`
- gitignore oracle still passes its existing tests after the path rename
- `command_exec_server` rejects `snapshot_cache_policy` in args with a clear
  error, and ignores `EPHEMERALOS_COMMAND_EXEC_SNAPSHOT_CACHE_POLICY`
- a fresh layer-stack root has no `materialized/` directory after a full
  shell + release cycle

Live verification (Daytona):

```bash
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_shell_call_isolation.py -q -s
```

Wall-clock parity check:

```text
Run a small live perf module that mirrors the Phase 04 A/B workload at
concurrency 1, 5, 10, 20 once. Assert each batch_wall_ms is within +5% of the
cache_disabled column from the latest Phase 04 summary
(.omc/results/live-e2e-phase04-shell-cache-ab-summary-20260506T190942Z.jsonl).
This is a smoke gate, not a new A/B; phase 04 already decided the policy.
```

## 7. Step Order and Verification

Each step lands on its own commit; the test in the right column must stay green
before moving on.

| Step | Change | Verify |
|---|---|---|
| 1 | LeaseRegistry: drop lowerdir bookkeeping | `test_lease_registry.py` |
| 2 | StackManager: collapse prepare_workspace_snapshot to acquire+materialize-transient; drop cache field accessors; drop cache eviction in release_lease | `test_snapshot_lease.py` |
| 3 | Delete `snapshot_cache.py` and `metrics.py`; delete `test_snapshot_cache.py`; update `test_import_fence.py` | `pytest test_layer_stack` |
| 4 | OCC ports rename `snapshot_cache_root` -> `gitignore_cache_root`; update gitignore_oracle | `pytest test_occ` |
| 5 | Runtime layer_stack_server / handlers / control ops: simplify response shape | `pytest test_runtime` |
| 6 | LayerStackClient: drop cache_policy, promote transient path | `pytest test_command_exec` |
| 7 | command_exec_server: drop _snapshot_cache_policy and env var; unconditional cleanup | `pytest test_api/test_shell.py` and `test_capture_to_occ_client.py` |
| 8 | Delete A/B test and snapshot_cache_metrics harness | live-e2e collection |
| 9 | Live smoke run against `test_shell_call_isolation.py` | green on Daytona |
| 10 | Update phase index, README, and Phase 02 plan to mark cache as removed | docs only |

## 8. Migration and Rollback

- The change is forward-only. The kept path is the production-equivalent
  cache-disabled path that already passed live e2e.
- Existing layer-stack roots may carry a `materialized/` directory from prior
  runs. `LayerStackManager.__init__` does a best-effort `rmtree` of that
  directory. This is safe: nothing reads from it after this phase.
- Rollback: `git revert` the phase commit. That restores Phase 02 cache plus
  Phase 04 cache_policy switch byte-for-byte; no data migration needed.

## 9. Risks and Open Questions

- `snapshot_cache_root` is currently the gitignore oracle's working directory.
  The rename to `gitignore_cache_root` is a name-only refactor, but the new
  storage path moves from `storage_root/materialized` to
  `storage_root/runtime/gitignore-cache`. Confirm no out-of-tree caller reads
  from `storage_root/materialized` directly.
- `LayerStackTransaction` and squash do not touch the cache today, but verify
  via grep before deletion that no test depends on the cache directory layout.
- The Phase 02 live e2e harness `test_workspace_snapshot_cache_leases.py`
  exercises lease/cache interactions. After this phase its cache assertions no
  longer hold; either delete it (preferred, the cache is gone) or downgrade it
  to lease-only assertions. Decision goes in Step 8.
- Live perf smoke gate uses `+5%` of the Phase 04 cache_disabled batch_wall_ms.
  If Daytona variance exceeds that, raise the bar to the higher of `+5%` or
  `+250 ms` so the gate does not flake.
- After removal, any future case for caching must produce a measurement that
  beats the Phase 04 decision bar (`p95 wall or batch wall improves by at
  least 250 ms or 20%`) on the workspace-replaced shell workload, not on
  prepare-only microbenchmarks.
