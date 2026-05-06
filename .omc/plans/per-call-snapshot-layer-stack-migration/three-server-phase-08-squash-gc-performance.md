# Phase 08 - Squash, GC, Cache, and Performance Gates

**Status:** draft implementation plan
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

## 1. Task Specification

Complete the maintenance and performance envelope for the three-server design.
Layer-stack must bound manifest depth, preserve leased readability, clean
unreferenced storage, and expose metrics proving shell setup and capture scale
with cached snapshot and changed bytes rather than total workspace size.

Implementation scope:

```text
add depth-bounded squash/checkpoint policy
preserve active leases through squash and GC
keep materialized lowerdirs pinned by leases
add cache hit/miss and lowerdir materialization metrics
add command-exec mount/capture timing metrics
add OCC prepare/commit/CAS retry metrics
add performance gates for cache-hit shell setup and capture walk
```

Out of scope:

```text
no real /testbed import during squash
no arbitrary middle-stack squash that invalidates leased manifests
no GC of active or leased data
no performance gate that depends on live provider availability for unit tests
```

Exit condition:

```text
squash can reduce active manifest depth without breaking active leases, GC
removes only unpinned data, and metrics prove cache-hit shell setup avoids full
workspace rematerialization.
```

## 2. Main Data Objects

```text
SquashPlan
  active_manifest_version
  keep_newest_prefix
  checkpoint_suffix
  expected_suffix_hash
  target_depth

CheckpointLayer
  layer_id
  root_hash
  source_manifest_version
  covered_layers

GarbageCollectionPlan
  active_manifest_pins
  lease_pins
  materialized_lowerdir_pins
  removable_layers
  removable_staging_dirs
  removable_lowerdirs

RuntimeMetrics
  import_s
  lowerdir_cache_hit
  lowerdir_materialize_s
  command_exec_mount_s
  command_exec_capture_s
  occ_prepare_s
  occ_commit_s
  occ_publish_cas_retry_count
```

## 3. File/Folder Structure Change

Target additions and updates:

```text
backend/src/sandbox/layer_stack/
|-- squash.py
|-- lease_budget.py
|-- snapshot_cache.py
+-- gc.py
+-- metrics.py

backend/src/sandbox/command_exec/
|-- workspace_mount.py
|-- capture/upperdir.py

backend/src/sandbox/occ/
|-- commit_transaction.py
|-- mutation_coordinator.py

backend/tests/unit_test/test_sandbox/test_layer_stack/
+-- test_squash_snapshot_cache.py
+-- test_gc_leases.py

backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/
|-- test_latency_attribution.py
|-- test_load_profiles.py
```

## 4. Workflow Demonstration

No active leases:

```text
active manifest:
  [L10, L09, L08, L07, L06, L05, L04, L03, L02, L01]
target depth = 1
  -> read merged layer-stack content from active manifest
  -> write checkpoint C11
  -> publish [C11]
  -> GC unreferenced old layers and lowerdirs
```

Active lease:

```text
lease A pins manifest N and lowerdir X
squash active manifest N+3
  -> may publish checkpoint for active history if suffix CAS still matches
  -> must keep layers and lowerdir X needed by lease A
GC
  -> skips leased manifest data and lowerdir X
release lease A
GC
  -> may remove X if no active manifest/cache policy still pins it
```

Suffix-CAS squash:

```text
t0 active [L05, L04, L03, L02, L01]
t1 plan keeps [L05, L04], checkpoints [L03, L02, L01]
t2 writer publishes [L06, L05, L04, L03, L02, L01]
t3 suffix still matches, publish [L06, L05, L04, B07]
```

Performance gate:

```text
shell cache hit:
  prepare_workspace_snapshot cache_hit=true
  mount_s bounded
  capture.walk_upperdir_s scales with changed paths
  no full workspace materialization
```

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `SquashPlan` | Makes active-manifest rewrite planning explicit before publish. |
| `CheckpointLayer` | Names a layer-stack storage compaction artifact, not a real filesystem import. |
| `GarbageCollectionPlan` | Separates planned deletes from observed pins. |
| `lease pins` | Leases constrain GC and lowerdir deletion, not whether active squash can proceed. |
| `cache hit/miss` | Performance reporting must distinguish materialization cost from mount/capture cost. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_squash_snapshot_cache.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_gc_leases.py -q
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py -q
```

Required assertions:

- no-leases squash can collapse active manifest to one checkpoint
- squash never reads real `/testbed`
- active leases remain readable through squash and GC
- GC keeps leased manifests and materialized lowerdirs
- cache-hit shell setup does not scale with total workspace size
- upperdir capture scales with changed paths/bytes
- metrics expose import, materialize, mount, command, capture, OCC prepare,
  OCC commit, and CAS retry timings
