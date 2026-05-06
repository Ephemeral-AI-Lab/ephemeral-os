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
evict materialized lowerdirs only from the lease release path
evict stale unleased layer dirs during squash or final stale lease release
remove public compact/sweep API from the runtime surface
add prepare_workspace_snapshot()
add lowerdir cache hit/miss metrics
```

Out of scope:

```text
no shell mount namespace yet
no OCC publish routing changes
no public squash/compact runtime API
```

Exit condition:

```text
two shell preparations for the same manifest reuse one lowerdir, release of the
last stale lease deletes that lowerdir, manifest advancement alone does not
evict lowerdir cache, and the latest lowerdir can be reused while the stack has
not moved forward. Public `api.compact` and sweep-style layer cleanup are not
part of the runtime surface.
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

Lease and eviction behavior:

```text
lease A pins manifest N lowerdir X
lease B pins manifest N lowerdir X
release A
  -> X remains pinned by B
release B
  -> if N is still latest, X may remain as the reusable latest cache
publish manifest N+1
  -> X is stale, but no lowerdir cache eviction runs without a lease release

lease C pins manifest M lowerdir Y
publish manifest M+1 while C is active
  -> Y remains pinned by C
release C
  -> Y is stale and has no remaining lease, so Y is evicted

squash rewrites old layers into checkpoint K
  -> old unleased layer dirs are removed immediately
  -> old leased layer dirs remain readable until their final lease releases
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
- release of the final lease keeps the lowerdir only if its manifest is still latest
- active-manifest advancement alone does not evict lowerdir cache
- release of the final lease evicts the lowerdir when its manifest is stale
- squash removes stale unleased layer dirs without a follow-up sweep call
- final stale lease release removes old layer dirs that were kept only for that
  lease
- runtime `OP_TABLE` does not register `api.compact`
- materialization metrics distinguish cache hit, miss, bytes, and duration
- cache-hit preparation does not walk the full workspace payload
