# Phase 08 - Squash, GC, and Performance Gates

**Status:** implemented and unit-verified after 2026-05-08 drift audit
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`
**Live checkout basis:** Phase 04.5 removed the materialized lowerdir cache; the
current code uses per-lease transient lowerdirs only.

## 0. Drift Correction

The old Phase 08 draft still assumed the Phase 02 materialized lowerdir cache
existed. That is no longer true in the live checkout.

Implemented now:

```text
backend/src/sandbox/layer_stack/stack_manager.py
  LayerStackManager.prepare_workspace_snapshot(request_id)
    acquires a WorkspaceLease
    materializes a fresh transient lowerdir under
      storage_root/runtime/transient-lowerdirs/<request>-<uuid>/lower
    returns PrepareWorkspaceSnapshotResult{lease_id, manifest, lowerdir, timings}

  LayerStackManager.release_lease(lease_id)
    releases lease bookkeeping
    removes only unreferenced layer refs from the released manifest
    does not own transient lowerdir cleanup

  LayerStackManager.squash(max_depth=...)
    delegates suffix planning/checkpoint construction to SquashWorker
    publishes a checkpoint manifest only if the live manifest still ends with
      the planned suffix
    removes only unreferenced layer refs after the active manifest rewrite

backend/src/sandbox/layer_stack/squash.py
  SquashPlan(active_version, suffix_to_checkpoint)
  SquashWorker.plan(...)
  SquashWorker.build_checkpoint(...)
  manifest_still_ends_with(...)

backend/src/sandbox/command_exec/workspace_mount.py
  workspace replacement execution
  timing key: command_exec.mount_workspace_s

backend/src/sandbox/runtime/command_exec_server.py
  prepares snapshot lease
  runs workspace-replaced command
  captures upperdir
  applies typed changes through OCCClient
  releases lease and deletes the transient lowerdir parent

backend/src/sandbox/runtime/handlers/metrics_handler.py
  api.layer_metrics returns manifest depth, active lease count, pinned layer
  count, layer/staging dir counts, storage bytes, and workspace binding fields
```

Not implemented and not a valid Phase 08 prerequisite:

```text
backend/src/sandbox/layer_stack/snapshot_cache.py
backend/src/sandbox/layer_stack/metrics.py
backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_cache.py
cache_policy / cache_hit / lowerdir_cache_* fields
WorkspaceLease.materialized_lowerdir
LeaseRegistry pinned lowerdir refcounts
api.compact or any public agent-visible stack-shrinking op
```

Phase 08 must not recreate the removed cache path. Future caching work needs a
fresh performance decision and must beat the Phase 04.5 decision bar on the
workspace-replaced shell workload, not on prepare-only microbenchmarks.

Implemented Phase 08 follow-up:

```text
SquashWorker.relabel_checkpoint(...)
  keeps checkpoint layer IDs aligned with the manifest version actually
  published when a concurrent prefix append lands between squash planning and
  active-manifest CAS.

LayerStackManager.prepare_workspace_snapshot(...)
  releases the lease and removes a partially materialized transient lowerdir if
  materialization fails before the caller receives the lease.

backend/tests/unit_test/test_sandbox/test_layer_stack/test_squash_gc.py
  covers active lease readability, release-time deletion guards, digest metadata
  cleanup, checkpoint relabeling, suffix-CAS prefix preservation, checkpoint
  discard on suffix mismatch, and no-cache GC boundaries.

backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_shell_lease_squash.py
  covers public live Daytona shell leases with concurrent public write/edit
  bursts, failed-shell lease/lowerdir cleanup, multi-path shell conflict
  all-or-nothing behavior, and no-cache timing fields without
  cache-hit/cache-policy payloads.
```

## 1. Task Specification

Complete the no-cache maintenance and performance envelope for the current
three-server design. Layer-stack must bound active manifest depth, preserve
leased snapshot readability, delete only unreferenced layer storage, and expose
timings that separate snapshot materialization, workspace mount/run, upperdir
capture, OCC prepare, OCC commit, and lock contention.

Implementation scope:

```text
harden the existing depth-bounded suffix squash policy
preserve active WorkspaceLease layer pins through squash and release-time GC
make layer deletion rules explicit around active_manifest + LeaseRegistry pins
keep transient lowerdir cleanup owned by command_exec_server, not layer GC
keep legacy storage_root/materialized cleanup as best-effort startup cleanup
add or tighten unit tests for squash + active lease + release-time deletion
assert existing shell/OCC/layer-stack timing keys in focused tests
use live performance gates that measure the no-cache transient path honestly
```

Out of scope:

```text
no materialized lowerdir cache
no cache-hit shell setup gate
no snapshot_cache.py or layer_stack.metrics.py
no lowerdir pins inside WorkspaceLease or LeaseRegistry
no arbitrary middle-stack squash that invalidates leased manifests
no real /testbed import during squash
no public api.compact / api.squash route
no performance gate that requires live provider availability for unit tests
```

Exit condition:

```text
squash can reduce active manifest depth without breaking active leases; layer
deletion removes only refs absent from both the active manifest and all active
lease manifests; command-exec deletes transient lowerdirs after release; and
metrics prove where no-cache shell time is spent without claiming cache hits.
```

## 2. Main Data Objects

Current data objects to keep:

```text
SquashPlan
  active_version
  suffix_to_checkpoint

LayerRef checkpoint
  layer_id             # B<manifest-version>-<uuid>
  path                 # layers/<layer_id>

WorkspaceLease
  lease_id
  manifest             # exact manifest pinned by the request
  owner_request_id
  acquired_at

PrepareWorkspaceSnapshotResult
  lease_id
  manifest_version
  root_hash
  manifest
  lowerdir             # transient path; caller deletes parent after release
  timings
```

Layer deletion inputs:

```text
candidate_layer_refs
active_manifest.layers
LeaseRegistry.pinned_layers()

delete candidate iff:
  candidate not in active_manifest.layers
  candidate not in LeaseRegistry.pinned_layers()
```

Timing keys already present and useful for Phase 08:

```text
layer_stack.materialize_s
layer_stack.prepare_workspace_snapshot.total_s
api.prepare_workspace_snapshot.total_s
command_exec.prepare_snapshot_s
command_exec.mount_workspace_s
command_exec.run_command_s
command_exec.capture_upperdir_s
command_exec.occ_apply_s
command_exec.release_snapshot_s
command_exec.total_s
api.shell.overlay_s
api.shell.occ_apply_s
api.shell.total_s
occ.prepare.total_s
occ.prepare.route_and_base_hash_s
occ.commit.total_s
occ.commit.publish_layer_s
occ.apply.commit_queue_wait_s
occ.apply.commit_worker_s
occ.apply.commit_s
occ.apply.total_s
layer_stack.transaction.lock_wait_s
layer_stack.transaction.lock_held_s
gitignore.cache_hits_total
gitignore.cache_misses_total
```

`api.layer_metrics` payload is the storage/lease status surface:

```text
manifest_version
manifest_depth
active_leases
pinned_layers
layer_dirs
staging_dirs
storage_bytes
workspace_bound
workspace_root
base_root_hash
```

## 3. File/Folder Structure Change

Existing files Phase 08 builds on:

```text
backend/src/sandbox/layer_stack/
|-- lease_registry.py
|-- stack_manager.py
|-- squash.py
|-- workspace_base.py
|-- workspace.py

backend/src/sandbox/command_exec/
|-- clients.py
|-- workspace_mount.py
`-- capture/
    |-- changeset.py
    `-- upperdir.py

backend/src/sandbox/runtime/
|-- command_exec_server.py
|-- layer_stack_handlers.py
|-- layer_stack_server.py
|-- occ_server.py
`-- handlers/
    |-- metrics_handler.py
    |-- read_handler.py
    |-- write_handler.py
    `-- edit_handler.py

backend/src/sandbox/occ/
|-- client.py
|-- commit_transaction.py
|-- orchestrator.py
|-- ports.py
|-- service.py
`-- serial_merger.py
```

Target updates:

```text
backend/src/sandbox/layer_stack/stack_manager.py
  keep _remove_unreferenced_layers as the release/squash GC boundary unless
  tests show it has grown enough to justify extraction
  ensure all deletion callers pass an explicit current_manifest
  keep transient lowerdir ownership out of this method

backend/src/sandbox/layer_stack/squash.py
  keep suffix-only planning and manifest_still_ends_with CAS semantics
  add minimal fields only if tests need clearer failure reporting

backend/src/sandbox/runtime/command_exec_server.py
  keep unconditional transient lowerdir cleanup after release
  assert timing payload includes prepare/mount/run/capture/OCC/release stages

backend/src/sandbox/runtime/handlers/metrics_handler.py
  keep layer_metrics storage/lease payload aligned with no-cache semantics
  do not add lowerdir cache metrics
```

Target tests:

```text
backend/tests/unit_test/test_sandbox/test_layer_stack/
|-- test_snapshot_lease.py          # update if needed for lease pins + cleanup
`-- test_squash_gc.py               # add focused squash/GC regression tests

backend/tests/unit_test/test_sandbox/test_command_exec/
|-- test_capture_to_occ_client.py   # transient cleanup and timing assertions
`-- test_write_edit_dispatch.py     # shared manager/lease registry assertions

backend/tests/unit_test/test_sandbox/
`-- test_import_fence.py            # keep cache modules absent from production imports

backend/tests/live_e2e_test/sandbox/layer_stack/
|-- test_squash.py
`-- test_layer_stack_load.py

backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/
|-- test_workspace_base_shell_lease_squash.py
|-- test_latency_attribution.py
`-- test_load_profiles.py
```

Do not add:

```text
backend/src/sandbox/layer_stack/gc.py       # unless the private deletion logic grows
backend/src/sandbox/layer_stack/metrics.py
backend/src/sandbox/layer_stack/snapshot_cache.py
backend/tests/unit_test/test_sandbox/test_layer_stack/test_squash_snapshot_cache.py
```

## 4. Workflow Demonstration

No active leases:

```text
active manifest:
  [L10, L09, L08, L07, L06, L05, L04, L03, L02, L01]
target depth = 2
  -> SquashWorker.plan(active, max_depth=2)
       suffix_to_checkpoint = [L09..L01]
  -> build checkpoint B11 from the suffix manifest only
  -> lock LayerStackManager
  -> re-read current manifest
  -> verify current manifest still ends with [L09..L01]
  -> publish active [L10, B11]
  -> _remove_unreferenced_layers([L09..L01], current_manifest=[L10, B11])
```

Active lease:

```text
lease A pins manifest N = [L05, L04, L03, L02, L01]
active manifest advances to [L08, L07, L06, L05, L04, L03, L02, L01]
squash active manifest
  -> may publish [L08, B09] if suffix CAS still matches
  -> must keep every LayerRef pinned by lease A
  -> may remove only old refs not in active manifest and not in pinned_layers()
release lease A
  -> release_lease removes layer refs from the lease manifest only when they are
     absent from both the current active manifest and remaining active leases
```

Transient lowerdir:

```text
command_exec_server
  -> prepare_workspace_snapshot(request_id)
       lowerdir = storage_root/runtime/transient-lowerdirs/<request>-<uuid>/lower
  -> run workspace-replaced command
  -> capture upperdir changes
  -> OCCClient.apply_changeset(...)
  -> release_lease(lease_id)
  -> rmtree(lowerdir.parent)
```

The transient lowerdir is not a layer-stack GC pin. It is a command-exec runtime
artifact tied to one shell request.

Suffix-CAS race:

```text
t0 active [L05, L04, L03, L02, L01]
t1 plan checkpoints suffix [L03, L02, L01], keeps live prefix [L05, L04]
t2 writer publishes [L06, L05, L04, L03, L02, L01]
t3 suffix still matches, publish [L06, L05, L04, B07]
```

If the suffix no longer matches, squash discards the checkpoint and returns
`None`.

Performance gate, no-cache path:

```text
shell call:
  prepare_workspace_snapshot:
    layer_stack.materialize_s
    command_exec.prepare_snapshot_s

  workspace replacement:
    command_exec.mount_workspace_s
    command_exec.run_command_s

  capture and publish:
    command_exec.capture_upperdir_s
    occ.prepare.total_s
    occ.commit.total_s
    occ.apply.total_s

  cleanup:
    command_exec.release_snapshot_s
```

This gate must not assert `cache_hit=true` or "no full workspace
materialization". The current design intentionally rebuilds a transient lowerdir
per shell call; Phase 08 should measure that cost separately and use squash to
bound manifest-depth-sensitive reads/materialization.

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `suffix_to_checkpoint` | Matches the live `SquashPlan`; squash coalesces only a suffix of the active manifest. |
| `manifest_still_ends_with` | Names the actual race check; this is suffix CAS, not arbitrary history rewriting. |
| `_remove_unreferenced_layers` | Existing private GC boundary; deletes layer refs only after active and lease pins are known. |
| `WorkspaceLease` | A lease pins exact manifest layers, not a lowerdir cache. |
| `transient lowerdir` | Names request-owned command-exec runtime state; it is deleted by the caller after release. |
| `api.layer_metrics` | Existing diagnostic payload for manifest depth, leases, pins, and storage counts. |

## 6. Tests and Exit Criteria

Focused unit verification:

```bash
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_snapshot_lease.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_layer_stack/test_squash_gc.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_command_exec/test_capture_to_occ_client.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_command_exec/test_write_edit_dispatch.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_import_fence.py -q
```

Live or live-adjacent verification:

```bash
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack/test_squash.py -q -s
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack/test_layer_stack_load.py -q -s
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_shell_lease_squash.py -q -s
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py -q -s
uv run pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_load_profiles.py -q -s
```

Required assertions:

- squash collapses active manifest depth through `LayerStackManager.squash`.
- squash does not read real `/testbed`; it materializes only from layer-stack
  manifests through `MergedView`.
- suffix race keeps concurrent prefix appends and returns `None` on suffix
  mismatch.
- active leases remain readable through squash and release-time layer deletion.
- layer deletion skips every ref in the active manifest and every ref returned
  by `LeaseRegistry.pinned_layers()`.
- `release_lease` does not delete transient lowerdirs; command-exec cleanup does.
- no new code imports or exposes `sandbox.layer_stack.snapshot_cache` or
  `sandbox.layer_stack.metrics`.
- no lowerdir cache metrics or `cache_hit` fields appear in shell,
  prepare-snapshot, or layer-metrics payloads.
- timing payloads expose snapshot materialization, workspace mount/run, upperdir
  capture, OCC prepare, OCC commit, OCC apply, release, and lock wait/held
  timings.
- live performance reports compare no-cache shell cost against the current
  transient baseline; they do not use cache-hit pass bars.

## 7. Step Order

| Step | Change | Verify |
|---|---|---|
| 1 | Add focused unit tests for squash with active leases and release-time deletion. | `test_squash_gc.py`, `test_snapshot_lease.py` |
| 2 | Tighten `LayerStackManager._remove_unreferenced_layers` only if tests expose a missing active/pinned guard. | layer-stack unit tests |
| 3 | Add timing-key assertions around command-exec shell result payloads. | `test_capture_to_occ_client.py` |
| 4 | Keep import fences aligned with Phase 04.5 cache removal. | `test_import_fence.py` |
| 5 | Run native/live squash and load probes; record no-cache timing breakdown. | live e2e commands above |
| 6 | Update phase index/README only if Phase 08 scope changes again. | docs diff review |

## 8. Risks and Open Questions

- The current copy-backed workspace replacement path copies the transient
  lowerdir into a per-run workspace. On platforms without private mount
  namespace support, shell setup can still scale with workspace size. Phase 08
  should measure this explicitly instead of attributing it to cache misses.
- There is no general orphan sweep for layers outside the immediate
  squash/release candidate set. Add one only if a concrete crash/orphan case is
  proven; keep the first pass scoped to active and leased refs.
- `fence_stale_staging` already removes stale staging dirs after daemon restart.
  Do not duplicate staging cleanup inside layer GC unless a real orphan pattern
  remains after that fence.
- The live latency attribution test currently notes that the public
  stack-shrinking path has been removed. If Phase 08 introduces an internal
  maintenance trigger, keep it runtime-internal and do not add an agent-visible
  compact tool without a separate API decision.
- Any future persistent cache proposal must clear the Phase 04.5 ROI bar:
  at least 250 ms or 20% improvement on p95 wall or batch wall for the
  workspace-replaced shell workload.
