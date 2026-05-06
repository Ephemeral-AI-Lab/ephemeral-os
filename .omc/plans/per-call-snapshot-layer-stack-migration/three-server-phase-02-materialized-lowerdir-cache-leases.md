# Phase 02 - Materialized Lowerdir Cache and Lease Pins

**Status:** draft implementation plan
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

## 1. Task Specification

Add the layer-stack snapshot preparation surface needed by guarded shell:
open a lease for the active manifest, materialize or reuse a read-only lowerdir,
pin it for the lease lifetime, and expose cache metrics.

Implementation scope:

```text
add materialized lowerdir cache keyed by manifest version and root hash
pin manifest and lowerdir through workspace leases
add prepare_workspace_snapshot()
add lowerdir cache hit/miss metrics
teach GC to preserve leased manifests and lowerdirs
```

Out of scope:

```text
no shell mount namespace yet
no OCC publish routing changes
no squash/checkpoint policy beyond preserving active leases
```

Exit condition:

```text
two shell preparations for the same manifest reuse one lowerdir, and GC cannot
delete a manifest or materialized lowerdir pinned by an active lease.
```

## 2. Main Data Objects

```text
WorkspaceLease
  lease_id
  workspace_ref
  manifest_version
  root_hash
  materialized_lowerdir
  owner_request_id
  expires_at

MaterializedSnapshot
  manifest_version
  root_hash
  lowerdir
  created_at
  refcount or pin ids
  byte_count

PrepareWorkspaceSnapshotResult
  lease_id
  manifest_version
  root_hash
  lowerdir
  cache_hit
  timings
```

## 3. File/Folder Structure Change

Target additions and updates:

```text
backend/src/sandbox/layer_stack/
+-- snapshot_cache.py
+-- lease_registry.py
+-- metrics.py
|-- stack_manager.py

backend/src/sandbox/runtime/
|-- layer_stack_server.py
|-- layer_stack_handlers.py

backend/tests/unit_test/test_sandbox/test_layer_stack/
+-- test_snapshot_cache.py
+-- test_lease_registry.py
```

## 4. Workflow Demonstration

```text
command-exec wants guarded shell snapshot
  -> layer-stack-server prepare_workspace_snapshot(request_id)
  -> read workspace binding and active manifest N
  -> open lease for N
  -> cache lookup key=(N, root_hash)
       hit: return existing read-only lowerdir
       miss: materialize merged view to lowerdir and cache it
  -> pin manifest N and lowerdir under lease_id
  -> return {lease_id, manifest N, lowerdir, cache_hit}
```

Lease and GC behavior:

```text
lease A pins manifest N lowerdir X
lease B pins manifest N lowerdir X
release A
  -> X remains pinned by B
collect_garbage
  -> keeps N and X
release B
collect_garbage
  -> may delete X if no active manifest/squash rule still needs it
```

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `prepare_workspace_snapshot` | Names the server operation command-exec needs; it returns an already leased workspace view. |
| `MaterializedSnapshot` | Keeps snapshot cache language in layer-stack, not command-exec. |
| `lowerdir` | Valid inside the mount/cache implementation because this is the read-only overlayfs input. |
| `WorkspaceLease` | Lease pins workspace manifests and lowerdirs, not whole sandbox state. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py -q
```

Required assertions:

- two leases for the same manifest reuse one materialized lowerdir
- release of one lease does not unpin a lowerdir still used by another lease
- GC keeps leased manifests and materialized lowerdirs
- materialization metrics distinguish cache hit, miss, bytes, and duration
- cache-hit preparation does not walk the full workspace payload
