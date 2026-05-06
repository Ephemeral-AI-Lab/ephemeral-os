# Phase 01 Live E2E Testing Plan - Workspace Binding and Base Layer

**Status:** draft
**Companion phase:** `three-server-phase-01-workspace-binding-base-layer.md`
**Scope owner:** `backend/tests/live_e2e_test/sandbox/`

## 1. Purpose

Phase 01 live E2E must prove that a real Daytona sandbox with an existing
`/testbed` repository is imported into a layer-stack base, then read and
assembled from layer-stack state instead of from the mutable real workspace.

The suite must not reuse old empty-stack load artifacts as evidence. A passing
phase-01 live artifact must include the imported base identity and enough
inventory/performance metadata to prove that the workload started from a real
base repository.

## 2. Current Gap

Existing live coverage has one useful current test:

```text
backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_read_load.py
```

It validates that `integrated_sandbox` clears runtime state, calls
`api.build_workspace_base`, and reads selected `/testbed` files through
`sandbox.api.tool.read_file`.

That is not enough for phase-01 sign-off because it does not measure import
cost, concurrent import behavior, snapshot assembly cost, layer creation cost,
or squash behavior over base-plus-layer stacks. Older integrated load artifacts
also predate the workspace-base import path and should be removed from phase-01
reports.

## 3. Required Test Layout

Add a phase-01-specific package so base-repo tests do not get mixed with older
Phase 3/4 integrated suites:

```text
backend/tests/live_e2e_test/sandbox/workspace_base/
|-- __init__.py
|-- test_base_import_cost.py
|-- test_base_import_concurrency.py
|-- test_base_import_correctness.py
|-- test_base_import_failure_safety.py
|-- test_layer_create_speed.py
|-- test_snapshot_assembly_speed.py
`-- test_squash_with_base_and_leases.py
```

Shared helpers:

```text
backend/tests/live_e2e_test/sandbox/_harness/
|-- workspace_base_probe.py       # native in-sandbox probe renderer
`-- workspace_base_metrics.py     # inventory, timing, and JSONL helpers
```

Keep or rewrite this public-read test as a compatibility smoke:

```text
backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_read_load.py
```

It should import shared metrics helpers and emit the same base identity fields
as the new phase-01 suite.

## 4. Harness Contract

All phase-01 tests run against a real Daytona sandbox from the configured image.

```text
live_sandbox
  -> setup_after_create(sandbox_id, "/testbed")
  -> runtime bundle available at /tmp/eos-sandbox-runtime

workspace_base_sandbox
  -> git reset --hard + git clean -fdx in /testbed
  -> rm -rf /tmp/eos-sandbox-runtime/layer-stack
  -> create /tmp/eos-sandbox-runtime/layer-stack
  -> no host-local LayerStackManager or OccService objects
```

Two execution shapes are allowed:

```text
public API:
  sandbox.api.tool.read_file
  runtime op api.build_workspace_base
  runtime op api.workspace_binding
  runtime op api.layer_metrics

native in-sandbox probe:
  cd /tmp/eos-sandbox-runtime
  python3 -c "<probe importing sandbox.layer_stack from runtime bundle>"
```

The pytest process must not import `sandbox.layer_stack`, `sandbox.occ`, or
`sandbox.overlay` for these live tests. Subsystem operations that need
`LayerStackManager`, manifests, leases, squash, or materialization must happen
inside the sandbox runtime bundle.

## 5. Artifact Contract

Every phase-01 live test that measures performance writes JSONL under:

```text
.omc/results/live-e2e-phase01-workspace-base-<case>-<utc>.jsonl
```

The first row is always a summary:

```json
{
  "schema": "sandbox.live_e2e.phase01_workspace_base.v1",
  "kind": "summary",
  "case": "base_import_cost",
  "workspace_root": "/testbed",
  "layer_stack_root": "/tmp/eos-sandbox-runtime/layer-stack",
  "base_manifest_version": 1,
  "base_root_hash": "<sha256>",
  "active_manifest_version": 1,
  "active_root_hash": "<sha256>",
  "repo_commit": "<git rev-parse HEAD>",
  "workspace_inventory": {
    "files": 0,
    "dirs": 0,
    "symlinks": 0,
    "bytes": 0,
    "sample_hashes": {}
  },
  "timings_ms": {},
  "pass_bars": {}
}
```

Per-call rows include:

```json
{
  "schema": "sandbox.live_e2e.phase01_workspace_base.v1",
  "kind": "call",
  "case": "base_import_cost",
  "label": "import_001",
  "success": true,
  "wall_ms": 0.0,
  "runtime_ms": 0.0,
  "timings": {},
  "resource": {}
}
```

Required metrics:

```text
api.workspace_base.total_s
workspace_base.collect_s
workspace_base.write_layer_s
workspace_base.write_manifest_s
workspace_base.write_binding_s
workspace_base.inventory.files
workspace_base.inventory.dirs
workspace_base.inventory.symlinks
workspace_base.inventory.bytes
layer_stack.publish.total_s
layer_stack.materialize.total_s
layer_stack.squash.total_s
```

If the current runtime does not expose a timing field, the test plan requires
adding that timing before claiming phase-01 performance coverage.

## 6. Coverage Matrix

### A. Base Import Cost

File:

```text
workspace_base/test_base_import_cost.py
```

Workflow:

```text
reset /testbed
clear layer-stack root
call api.build_workspace_base(workspace_root="/testbed")
fetch api.workspace_binding
fetch api.layer_metrics
compute independent /testbed inventory via raw_exec find/stat/hash
emit JSONL summary and call row
```

Assertions:

- `base_manifest_version == 1`
- `active_manifest_version == 1`
- `base_root_hash == active_root_hash`
- `workspace_root == "/testbed"`
- `layer_stack_root` is outside `/testbed`
- inventory has nonzero files, dirs, and bytes
- selected file hashes from layer-stack reads match raw `/testbed`
- `workspace.json` exists only after manifest version 1 is published

Performance output:

- import wall p50/p99 over `N` sequential rebuilds, default `N=5`
- collection, write-layer, manifest-write, binding-write timing breakdown
- bytes/sec and files/sec

No hard budget in the first implementation. The first live run establishes a
baseline; later runs can add redlines.

### B. 20 Concurrent Base Builds

File:

```text
workspace_base/test_base_import_concurrency.py
```

Two cases are required because they answer different questions.

Independent roots:

```text
for i in 0..19:
  layer_stack_root=/tmp/eos-sandbox-runtime/layer-stack-phase01-c20/i
launch 20 api.build_workspace_base calls concurrently
verify all 20 bindings and manifests
verify all 20 base_root_hash values match
emit aggregate p50/p99/max and batch wall
```

Same root race:

```text
layer_stack_root=/tmp/eos-sandbox-runtime/layer-stack-race
launch 20 api.build_workspace_base calls concurrently
accepted outcomes:
  exactly one build succeeds and the rest fail closed with existing-base errors
  or all callers converge through api.ensure_workspace_base
rejected outcome:
  partial manifest, partial base layer, orphan staging, inconsistent root hash
```

Assertions:

- no orphan staging dirs after the race
- final manifest is version 1
- final binding points at `/testbed`
- no caller observes an empty active manifest as success

### C. Squash With Base, With And Without Leases

File:

```text
workspace_base/test_squash_with_base_and_leases.py
```

No lease case:

```text
build base
publish layer shapes over the base:
  append-only new files
  overwrite existing base files
  delete base files
  symlink change
  opaque directory
measure squash for depths 5, 20, 100, 200
verify active view before and after squash is byte-identical
verify depth decreases
verify no orphan staging
```

Lease case:

```text
build base
publish layers A and B
acquire lease for snapshot after A
publish layers C..N
squash active stack
read leased snapshot and active snapshot
verify leased snapshot still sees A-state
verify active snapshot sees N-state
release lease
run GC
verify reclaimable old layers are removed only after lease release
```

Assertions:

- squash never invalidates an active lease
- no-lease squash preserves active view
- lease-protected layers remain pinned until release
- GC removes only unpinned orphan layers/staging

### D. New Layer Creation Speed

File:

```text
workspace_base/test_layer_create_speed.py
```

Workloads over an imported base:

```text
1 small file
100 small files
1 large file, default 2 MiB
100 overwrites of existing base files
50 deletes of existing base files
mixed write/overwrite/delete/symlink batch
```

Measure:

- layer creation wall
- `layer_stack.publish.total_s`
- digest/write/manifest sub-stages
- resulting manifest depth
- storage bytes delta

Assertions:

- every published path is visible in active merged view
- overwritten base content resolves to top layer
- deleted base content is hidden
- layer creation does not mutate real `/testbed`

### E. Assemble Base Repo Plus Layers Into Workspace Snapshot

File:

```text
workspace_base/test_snapshot_assembly_speed.py
```

Workflow:

```text
build base from /testbed
publish synthetic layers of depth 0, 1, 5, 20, 100, 200
materialize active snapshot to a temp directory outside /testbed
return manifest version, root hash, inventory, and selected file hashes
repeat cold and warm materialization
```

Required cases:

- base only
- base plus append layers
- base plus overwrites
- base plus deletes
- base plus symlinks
- base plus opaque dirs

Assertions:

- materialized snapshot inventory matches merged-view inventory
- selected file hashes match expected top-layer semantics
- materialized snapshot does not read from or write to real `/testbed`
- cold and warm metrics are emitted separately

### F. Base Import Correctness

File:

```text
workspace_base/test_base_import_correctness.py
```

Assertions:

- raw `/testbed` file count equals base-layer file count for representable files
- raw `/testbed` directory count equals base-layer directory count
- symlink targets round-trip
- binary file hashes round-trip
- empty directories round-trip
- unicode paths and long paths round-trip when present
- no Git or gitignore classification is present in the base importer output

This test should use a bounded sample for content hashes by default, with an
environment variable for full inventory:

```text
EPHEMERALOS_PHASE01_FULL_INVENTORY=1
```

### G. Base Import Failure Safety

File:

```text
workspace_base/test_base_import_failure_safety.py
```

Cases:

- special file inside workspace
- file disappears during import
- file content changes during import
- new file appears during import
- layer stack root inside `/testbed`
- existing manifest or existing workspace binding

Assertions:

- failure publishes no `workspace.json`
- failure publishes no active manifest version 1 unless the build fully
  succeeded
- staging dirs are removed or marked orphan and cleaned by the next reset
- error kind is stable enough for tests to assert fail-closed behavior

The current implementation is expected to need a second-scan or quiescence fix
before the "new file appears during import" case can pass.

## 7. Old Coverage Removal

Remove these as phase-01 evidence:

```text
.omc/results/live-e2e-integrated-*.jsonl
.omc/results/live-e2e-phase3-concurrency-scaling-*.jsonl
.omc/results/live-e2e-phase3-per-call-timings-*.jsonl
```

Do not delete the historical files blindly if they are useful for older phase
reports. Instead:

- stop citing them from phase-01 reports
- move phase-01 reporting to the new
  `sandbox.live_e2e.phase01_workspace_base.v1` schema
- rewrite any current README line that says "current" when it points at an
  artifact generated before `Add layer-stack workspace base binding`
- keep later Phase 3/4 tests only after rerunning them with a fixture that
  proves `base_manifest_version == 1` before measured operations

If an old test only builds an empty `LayerStackManager` in the sandbox and never
imports `/testbed`, delete it or move it to native subsystem unit-like coverage.
It must not remain in the phase-01 E2E suite.

## 8. Commands

Focused phase-01 live run:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/workspace_base \
  -v -rs -s --tb=short
```

Compatibility smoke:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_read_load.py \
  -v -rs -s --tb=short
```

Fast local collection gate:

```bash
.venv/bin/pytest --collect-only backend/tests/live_e2e_test/sandbox/workspace_base -q
```

## 9. Phase 01 Pass Bar

Phase 01 live E2E is complete only when:

- all tests under `backend/tests/live_e2e_test/sandbox/workspace_base/` pass
  against a real Daytona sandbox
- every performance test emits the phase-01 JSONL schema
- base import cost is measured directly, not inferred from read-load timing
- 20 independent base builds complete and produce matching base hashes
- same-root concurrent build fails closed or converges through
  `ensure_workspace_base`
- squash preserves active views with no leases and preserves leased views with
  leases
- new layer creation and snapshot materialization are measured over a real
  imported `/testbed` base
- public `read_file` proves it reads layer-stack content after raw `/testbed`
  mutation
- no phase-01 report cites pre-base-import empty-stack artifacts as current
  evidence

## 10. Implementation Order

1. Add workspace-base harness helpers and artifact writer.
2. Rewrite `test_workspace_base_read_load.py` to use the new artifact schema.
3. Add base import cost and correctness tests.
4. Add failure-safety tests, including mid-import mutation.
5. Add independent and same-root concurrent build tests.
6. Add layer creation speed tests over imported base.
7. Add snapshot assembly speed tests over imported base plus layers.
8. Add squash with/without lease tests over imported base plus layers.
9. Remove stale phase-01 README/report references to old empty-stack artifacts.
10. Run focused live suite and write a phase-01 implementation/performance
    report with the new artifact paths.
