# Phase 02 Live E2E Testing Plan - Materialized Lowerdir Cache and Lease Pins

**Status:** implemented and live-verified
**Companion phase:** `three-server-phase-02-materialized-lowerdir-cache-leases.md`
**Implementation report:** `three-server-phase-02-materialized-lowerdir-cache-leases-implementation-report.md`
**Scope owner:** `backend/tests/live_e2e_test/sandbox/`

## 1. Purpose

Phase 02 live E2E must prove that a real Daytona sandbox can prepare and release
leased layer-stack workspace snapshots through the runtime API surface, not only
through process-local unit tests.

The phase-02 feature is a COW workspace plus a disk-backed, derived lowerdir
cache:

```text
durable truth:
  layer-stack manifest + immutable layers

derived acceleration:
  materialized/<manifest-version>-<root-hash>/lower
```

The cache is not workspace truth. It is a rebuildable, read-only merged view
used by later command-exec work. The live suite must prove the creation policy,
the eviction policy, and the performance value of that derived lowerdir:

- first prepare for a manifest creates exactly one materialized lowerdir
- repeated prepare for the same latest manifest reuses that lowerdir
- concurrent prepare for the same manifest fans into one final lowerdir
- leases pin lowerdirs while requests are active
- stale lowerdirs are evicted only by the lease release path
- cache hits avoid rematerialization and improve prepare latency versus cold
  materialization in the same live sandbox run

The eviction rule is:

```text
latest and unleased:
  keep for reuse while the active manifest has not moved

stale and leased:
  keep until the last lease drops

stale and unleased:
  keep unless it becomes stale during a final lease release event; no manifest
  advance or background sweep visits it

lease release event:
  after LeaseRegistry.release, if the released lowerdir has no remaining pins
  and the released manifest is no longer latest, remove that lowerdir
```

The creation rule is:

```text
no cache entry for manifest:
  prepare creates one materialized lowerdir and returns cache_hit=false

cache entry exists for latest manifest:
  prepare reuses it and returns cache_hit=true

cache entry exists for stale manifest:
  it may remain on disk, but no new prepare selects it; only still-active
  leases that already reference it can use it

new manifest identity:
  prepare is a miss and creates a new lowerdir only for that new identity

manifest advancement:
  does not evict lowerdir cache by itself
```

This plan does not validate the Phase 04 workspace-replaced shell mount. Phase
02 validates the runtime snapshot preparation primitive that Phase 04 will
consume.

## 2. Original Gap Covered By This Plan

The Phase 02 implementation report is accurate as unit verification:

```text
unit tests cover:
  MaterializedSnapshotCache
  LeaseRegistry lowerdir refcounts
  LayerStackManager.prepare_workspace_snapshot()
  OP_TABLE registration
```

That is not enough for live sign-off because the implementation also added
runtime/control entry points:

```text
api.prepare_workspace_snapshot
api.release_workspace_snapshot
api.layer_metrics lowerdir cache metrics
release-time stale lowerdir eviction
no manifest-advance lowerdir cache eviction
```

Live evidence covered by this plan:

- a real Daytona-backed call to `api.prepare_workspace_snapshot`
- proof that lowerdir cache directories exist inside the sandbox filesystem
- proof that first prepare creates exactly one materialized lowerdir
- proof that same-manifest cache hits do not create another lowerdir
- proof that `api.layer_metrics` exposes live cache hit/miss and pin state
- proof that the latest lowerdir remains reusable after leases drain
- proof that manifest advancement alone leaves lowerdir cache untouched
- proof that final stale lease release deletes the materialized lowerdir
- disk-size evidence for materialized snapshots on the sandbox volume
- paired cold-miss versus warm-hit timing evidence for cache performance
- repeated-manifest versus changed-manifest behavior through public runtime
  calls

## 3. Required Test Layout

Add one focused public-runtime live test module:

```text
backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/
`-- test_workspace_snapshot_cache_leases.py
```

Optional helper module if the test grows beyond simple local helpers:

```text
backend/tests/live_e2e_test/sandbox/_harness/
`-- snapshot_cache_metrics.py
```

The test must live under the existing live E2E suite so
`backend/tests/live_e2e_test/conftest.py` continues to enforce the import fence.
The host pytest file must not import:

```text
sandbox.layer_stack
sandbox.occ
sandbox.overlay
```

Host-side test code may use:

```text
sandbox.api.tool._runtime.call_runtime_api
sandbox.api.tool.read_file/write_file/edit_file/shell wrappers through the fixture
integrated_sandbox.raw_exec for side-channel filesystem inspection only
```

## 4. Harness Contract

All Phase 02 live tests run against the real Daytona-backed fixture:

```text
integrated_sandbox
  -> setup_after_create(sandbox_id, "/testbed")
  -> reset /testbed through git reset + clean
  -> rm -rf /tmp/eos-sandbox-runtime/layer-stack
  -> api.build_workspace_base(workspace_root="/testbed")
```

The test must use public runtime/API paths for behavior under test:

```text
prepare snapshot:
  runtime op api.prepare_workspace_snapshot

release snapshot:
  runtime op api.release_workspace_snapshot

metrics:
  runtime op api.layer_metrics

manifest mutation setup:
  sandbox.api.tool.write_file or edit_file
```

The live suite must not call `api.compact`; Phase 02 lowerdir cache cleanup is
event-driven through `api.release_workspace_snapshot`. Manifest advancement
creates new cache identities but does not evict old lowerdir cache by itself.

`raw_exec` is allowed only for observation that is outside the public API:

```text
test -d <returned-lowerdir>
cat <returned-lowerdir>/<path>
du -sb <layer-stack-root>/materialized
df -Pk <layer-stack-root>
find <layer-stack-root>/materialized -maxdepth ...
```

Do not use host-local `LayerStackManager`, `OccService`, or direct provider
objects to create the layer state being validated.

## 5. Artifact Contract

Every Phase 02 live test that measures cache size or performance writes JSONL
under:

```text
.omc/results/live-e2e-phase02-snapshot-cache-leases-<case>-<utc>.jsonl
```

The first row is a summary:

```json
{
  "schema": "sandbox.live_e2e.phase02_snapshot_cache_leases.v1",
  "kind": "summary",
  "case": "reuse_and_stale_eviction",
  "workspace_root": "/testbed",
  "layer_stack_root": "/tmp/eos-sandbox-runtime/layer-stack",
  "active_manifest_version": 1,
  "root_hash": "<sha256>",
  "materialized_lowerdirs": 1,
  "active_leases": 0,
  "pinned_lowerdirs": 0,
  "cache_creation": {
    "created_lowerdirs": 1,
    "reused_lowerdirs": 1,
    "unexpected_extra_lowerdirs": 0
  },
  "cache_bytes_before": 0,
  "cache_bytes_after_prepare": 0,
  "cache_bytes_after_eviction": 0,
  "df_kb_available_before": 0,
  "df_kb_available_after_eviction": 0,
  "lowerdir_cache": {
    "lowerdir_cache_hits": 0,
    "lowerdir_cache_misses": 0,
    "lowerdir_cache_materialized_bytes": 0
  },
  "performance": {
    "cold_miss_samples": 0,
    "warm_hit_samples": 0,
    "median_cold_miss_wall_ms": 0.0,
    "median_warm_hit_wall_ms": 0.0,
    "median_materialize_ms_saved": 0.0,
    "warm_hit_faster_than_cold_miss": false
  },
  "timings_ms": {},
  "pass_bars": {}
}
```

Per-call rows include:

```json
{
  "schema": "sandbox.live_e2e.phase02_snapshot_cache_leases.v1",
  "kind": "call",
  "case": "reuse_and_stale_eviction",
  "label": "prepare_a",
  "op": "api.prepare_workspace_snapshot",
  "success": true,
  "cache_hit": false,
  "manifest_version": 1,
  "root_hash": "<sha256>",
  "lowerdir": "/tmp/eos-sandbox-runtime/layer-stack/materialized/...",
  "materialized_byte_count": 0,
  "cache_dir_count_before": 0,
  "cache_dir_count_after": 1,
  "created_lowerdir": true,
  "wall_ms": 0.0,
  "timings": {}
}
```

Required timing and size fields:

```text
api.prepare_workspace_snapshot.total_s
layer_stack.prepare_workspace_snapshot.total_s
layer_stack.snapshot_cache.lookup_s
layer_stack.snapshot_cache.materialize_s       # miss only
layer_stack.snapshot_cache.bytes
api.layer_metrics.lowerdir_cache.*
cache_dir_count_before
cache_dir_count_after
cache_bytes_before
cache_bytes_after_prepare
cache_bytes_after_eviction
df_kb_available_before
df_kb_available_after_eviction
paired_cold_miss_wall_ms
paired_warm_hit_wall_ms
median_cold_miss_wall_ms
median_warm_hit_wall_ms
median_materialize_ms_saved
```

If a field is not currently exposed, the test implementation must add the
smallest runtime metric needed before claiming live performance coverage.

## 6. Coverage Matrix

The suite should keep two kinds of assertions separate:

```text
cache policy assertions:
  creation count, reuse identity, lease pins, eviction timing

cache performance assertions:
  cold miss cost, warm hit cost, materialization avoided, same-run delta
```

### A. Cache Creation, Latest Reuse, And Manifest-Advance Non-Eviction

File:

```text
layer_stack_overlay_occ/test_workspace_snapshot_cache_leases.py
```

Test:

```text
test_latest_snapshot_cache_reuses_and_manifest_advance_does_not_evict
```

Workflow:

```text
use integrated_sandbox
write tracked/cache-reuse.txt through sandbox.api.tool.write_file
record materialized directory count
call api.prepare_workspace_snapshot(request_id="lease-a")
record materialized directory count
call api.prepare_workspace_snapshot(request_id="lease-b")
record materialized directory count
fetch api.layer_metrics
inspect returned lowerdir through raw_exec
release lease-a
verify lowerdir still exists
release lease-b
verify lowerdir still exists because manifest is still latest
call api.prepare_workspace_snapshot(request_id="lease-c")
verify cache_hit=true and lowerdir is reused
release lease-c
write tracked/cache-reuse.txt = "v2" through sandbox.api.tool.write_file
verify old lowerdir still exists because no release observed it as stale
fetch api.layer_metrics
emit JSONL artifact
```

Assertions:

- first prepare succeeds and `cache_hit == false`
- first prepare creates exactly one materialized lowerdir
- second prepare succeeds and `cache_hit == true`
- second prepare does not create another materialized lowerdir
- both prepares return the same `manifest_version`
- both prepares return the same `root_hash`
- both prepares return the same `lowerdir`
- lowerdir exists inside the sandbox after both prepares
- expected workspace file exists under the lowerdir
- metrics show `active_leases == 2`
- metrics show `pinned_lowerdirs == 1`
- metrics show `materialized_lowerdirs == 1`
- lowerdir cache metrics show one miss and one hit
- cache creation summary records one created lowerdir and one reused lowerdir
- after releasing one lease, the lowerdir still exists
- after releasing all leases while the manifest is still latest, the lowerdir
  still exists
- a new prepare against the unchanged latest manifest reuses the lowerdir
- after a public write advances the manifest, the old unleased lowerdir still
  exists because manifest advancement is not an eviction event
- final metrics show no active leases and no pinned lowerdirs

### B. Stale Final Lease Eviction

Test:

```text
test_stale_snapshot_cache_evicts_on_final_release
```

Workflow:

```text
write tracked/cache-stale.txt = "v1"
prepare snapshot A and keep its lease active
write tracked/cache-stale.txt = "v2"
verify lowerdir A still exists while its lease is active
release snapshot A
verify lowerdir A is removed immediately
```

Assertions:

- snapshot A is pinned through the manifest change
- manifest change does not remove a lowerdir with an active lease
- final release of stale snapshot A removes its lowerdir immediately
- no separate eviction call is required

### C. Changed Manifest Misses

Test:

```text
test_prepare_workspace_snapshot_misses_after_manifest_change
```

Workflow:

```text
write tracked/cache-version.txt = "v1"
prepare snapshot A
release snapshot A
write tracked/cache-version.txt = "v2"
prepare snapshot B
fetch api.layer_metrics
inspect lowerdir B
release snapshot B
write tracked/cache-version.txt = "v3"
verify snapshot B remains because release happened while B was latest
```

Assertions:

- snapshot B has a different `manifest_version` from snapshot A
- snapshot B has a different `root_hash` from snapshot A
- snapshot B is a cache miss
- snapshot B lowerdir sees `v2`
- old materialized lowerdir remains after the manifest advances to `v2`
- snapshot B remains reusable after release while it is latest
- snapshot B remains after the manifest advances to `v3`

### D. Concurrent Prepare Fan-In

Test:

```text
test_concurrent_prepare_same_manifest_fans_into_one_lowerdir
```

Default concurrency:

```text
N = 10
```

Workflow:

```text
seed one active manifest through public write_file
launch N api.prepare_workspace_snapshot calls concurrently
fetch api.layer_metrics
release all leases
verify shared lowerdir remains reusable while latest
write one public change to advance the manifest
verify shared lowerdir still exists because release already happened
```

Assertions:

- all prepares succeed
- all prepares return the same `manifest_version`
- all prepares return the same `root_hash`
- all prepares return the same `lowerdir`
- exactly one call is a miss and the rest are hits
- final materialized directory count is one, not `N`
- metrics show `active_leases == N` before release
- metrics show `pinned_lowerdirs == 1`
- final lease release does not evict the lowerdir while it is still latest
- manifest advancement does not evict the old unleased lowerdir

If the runtime later allows true multi-process materialization races, this test
may need to accept multiple miss attempts only if final state still converges to
one valid lowerdir and no failed staging dirs remain. Under the current resident
runtime daemon, the expected result is one miss and `N - 1` hits.

### E. Deep Manifest Cache Size

Test:

```text
test_deep_manifest_materializes_one_cache_entry_not_one_per_layer
```

Default depth:

```text
depth = 20
```

Extended profile:

```text
EPHEMERALOS_LIVE_E2E_PHASE02_DEPTH=100
```

Workflow:

```text
publish depth small layers through public write_file
fetch api.layer_metrics and assert manifest depth
prepare one snapshot
inspect materialized directory count and cache size
release lease
verify latest lowerdir remains reusable
publish one more layer
verify old lowerdir remains because manifest advancement is not an eviction event
```

Assertions:

- depth greater than one is visible in layer metrics
- one prepare creates one materialized lowerdir, not one lowerdir per layer
- `materialized_lowerdirs == 1` after prepare
- cache byte count is recorded
- release does not remove the lowerdir while that manifest is latest
- manifest advancement does not remove the old lowerdir

This test answers the common confusion:

```text
depth=100 does not mean 100 cache dirs
depth=100 means one cache dir for that manifest identity
```

### F. Cache Performance Improvement Evidence

Test:

```text
test_cache_hit_reduces_prepare_cost_for_same_manifest
```

Default samples:

```text
EPHEMERALOS_LIVE_E2E_PHASE02_PERF_PAIRS=5
EPHEMERALOS_LIVE_E2E_PHASE02_PERF_MB=16
```

Workflow:

```text
for each pair:
  write a unique payload through public write_file to create a new manifest
  record materialized directory count and cache bytes
  prepare snapshot cold miss
  release cold lease while this manifest is still latest
  prepare snapshot warm hit for the same manifest
  advance manifest with a small public write while warm lease is active
  release warm lease to evict the now-stale lowerdir
  record materialized directory count and cache bytes after eviction
emit paired cold/warm timing rows and one summary row
```

Assertions:

- every cold prepare has `cache_hit == false`
- every warm prepare has `cache_hit == true`
- cold rows include materialization duration and byte count
- warm rows do not run materialization and do not create a new lowerdir
- warm rows return the same `manifest_version`, `root_hash`, and `lowerdir` as
  their paired cold row
- median warm-hit `api.prepare_workspace_snapshot` wall time is lower than
  median cold-miss wall time in the same sandbox run
- median materialization time avoided is recorded as the primary cache benefit
- performance summary records sample count, medians, min/max, and the cold/warm
  delta

This test is the required evidence that the cache helps. The first live artifact
does not set a permanent latency budget, but it must prove that cache hits avoid
materialization and are faster than cold materialization for the configured
payload in the same Daytona sandbox.

### G. Disk Budget And Eviction Evidence

Test:

```text
test_cache_size_is_observable_and_eviction_returns_unpinned_space
```

Workflow:

```text
record df -Pk for layer_stack_root
write a configurable payload file through public write_file
prepare snapshot
record du -sb materialized
advance manifest with a small public write
record du -sb materialized while stale lease is still pinned
release lease
record du -sb materialized and df -Pk again
```

Default payload:

```text
EPHEMERALOS_LIVE_E2E_PHASE02_LARGE_MB=16
```

Assertions:

- materialized cache bytes increase after prepare
- `materialized_byte_count` is at least the payload size
- after manifest advancement with the lease still active, materialized cache bytes
  remain pinned
- after final stale lease release, old materialized cache bytes return to zero or
  near metadata-only size
- the test does not intentionally fill the sandbox volume
- disk observations are attached to both the creation case and the eviction case

Out of scope for this live test:

```text
forcing ENOSPC by filling the Daytona volume
```

Disk-full behavior should be covered by a narrow unit test with an injected
materializer that raises `OSError(errno.ENOSPC, ...)`, plus a later production
hardening task for cache byte limits and retry-after-eviction.

### H. Runtime Error Shape For Missing Binding

Test:

```text
test_prepare_workspace_snapshot_fails_closed_without_workspace_binding
```

Workflow:

```text
call api.prepare_workspace_snapshot against an empty temporary layer_stack_root
```

Assertions:

- runtime call fails
- error explains missing workspace binding or empty active manifest
- no cache directory is created
- no lease remains pinned

This test should use a temporary sandbox path outside the default layer stack
root so it does not corrupt other live tests.

## 7. Pass Bars

Hard correctness pass bars:

- live tests use a real Daytona sandbox
- host test modules obey the live-suite import fence
- runtime `OP_TABLE` does not register `api.compact`
- snapshot preparation goes through `api.prepare_workspace_snapshot`
- release goes through `api.release_workspace_snapshot`
- manifest mutations used for setup go through public guarded APIs
- first prepare for a manifest creates exactly one materialized lowerdir
- same manifest identity reuses one lowerdir
- same manifest cache hits do not create another lowerdir
- changed manifest identity creates a cache miss
- latest unleased lowerdir remains reusable while the active manifest has not
  advanced
- active leases pin stale lowerdirs through manifest advancement
- final stale lease release removes the materialized cache immediately
- manifest advancement alone does not evict materialized lowerdir caches
- depth N creates one cache entry for one prepared manifest identity

Metrics pass bars:

- every prepare row records cache hit or miss
- every prepare row records materialized directory count before and after the
  call
- summary records created, reused, and unexpected extra lowerdir counts
- miss rows include materialization duration and byte count
- hit rows include lookup duration and omit materialization duration
- summary records lowerdir cache hits, misses, materialized bytes, active
  leases, pinned lowerdirs, materialized lowerdirs, cache bytes, and free disk
  observations

Performance pass bars:

- performance evidence is collected from paired cold-miss and warm-hit prepares
  in the same sandbox run
- warm-hit rows must skip materialization
- median warm-hit prepare wall time must be lower than median cold-miss prepare
  wall time for the configured payload
- median materialization time avoided is reported separately from total wall time
- first live run establishes the initial budget baseline
- no hard p50/p99 budget until at least one Daytona artifact is collected
- ratio-only performance gates are not acceptable
- later budgets should use absolute setup time and cache byte ceilings

## 8. Verification Commands

Collection check:

```bash
uv run pytest backend/tests/live_e2e_test --collect-only -q
```

Focused Phase 02 live suite:

```bash
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_snapshot_cache_leases.py -q
```

Focused unit regressions that must remain green:

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py -q
```

Runtime registration sanity:

```bash
uv run python -c "from sandbox.runtime.server import OP_TABLE; assert 'api.compact' not in OP_TABLE; assert 'api.prepare_workspace_snapshot' in OP_TABLE; assert 'api.release_workspace_snapshot' in OP_TABLE"
```

## 9. Sign-Off Checklist

- [x] New live test module added under `layer_stack_overlay_occ/`
- [x] Host test file passes the import fence
- [x] Runtime registration sanity proves `api.compact` is absent
- [x] Live test calls `api.prepare_workspace_snapshot`
- [x] Live test calls `api.release_workspace_snapshot`
- [x] Live test records lowerdir cache metrics
- [x] Live test proves first prepare creates exactly one lowerdir
- [x] Live test proves warm prepare creates no extra lowerdir
- [x] Live test proves same-manifest reuse
- [x] Live test proves changed-manifest miss
- [x] Live test proves latest lowerdir reuse after final lease release
- [x] Live test proves manifest advancement alone does not evict lowerdir cache
- [x] Live test proves final stale lease release removes the lowerdir immediately
- [x] Live test records cache byte size and available disk observations
- [x] Live test records paired cold-miss and warm-hit timing evidence
- [x] Live test proves cache hits skip materialization
- [x] Live test proves median warm-hit prepare time is lower than median cold-miss
      prepare time for the configured payload
- [x] JSONL artifact uses `sandbox.live_e2e.phase02_snapshot_cache_leases.v1`
- [x] Implementation report updated from `unit-verified` to include live E2E
      evidence only after the live run passes
