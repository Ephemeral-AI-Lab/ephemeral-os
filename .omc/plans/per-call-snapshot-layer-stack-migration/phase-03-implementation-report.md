# Phase 03 - OCC Changeset API And Routing Implementation Report

Companion to
[`phase-03-occ-changeset-routing.md`](./phase-03-occ-changeset-routing.md).
This report records the Phase 03 OCC preparation surface delivered in the
current checkout, the tests added around it, and the work intentionally left for
later phases.

---

## 1. Verdict

**Phase 03 is implemented and verified as a preparation/routing layer.**

OCC now has a service-backed changeset path that accepts typed mutation intent
objects, routes normalized paths into tracked/direct/drop/reject groups, infers
tracked base hashes from a leased `Manifest`, and returns a
`PreparedChangeset` for the future Phase 04 commit transaction.

The implementation deliberately does not publish layers or perform final active
manifest validation. The older live-root OCC apply modules remain present for
current runtime callers until the Phase 06 cutover removes or reroutes them.

---

## 2. File Inventory

### Runtime Package

| File | Purpose |
| --- | --- |
| `backend/src/sandbox/occ/changeset/types.py` | Source-tagged `Change`, `WriteChange`, `EditChange`, and `DeleteChange` values, with compatibility properties for the legacy gate |
| `backend/src/sandbox/occ/changeset/builders.py` | Builders for API write/edit/delete and shell-captured write/delete changes |
| `backend/src/sandbox/occ/changeset/prepared.py` | `PreparedChangeset`, `PreparedPathGroup`, `RouteDecision`, and `ChangesetOptions` |
| `backend/src/sandbox/occ/routing/gitignore.py` | Cached `git check-ignore` oracle under the OCC routing namespace |
| `backend/src/sandbox/occ/routing/router.py` | `ChangeRouter` path normalization, gitignore routing, drop/reject policy, grouping, and concurrent preparation |
| `backend/src/sandbox/occ/runtime_ops.py` | Shared byte hash and leased-manifest base-hash inference helpers |
| `backend/src/sandbox/occ/service.py` | `OccService.apply_changeset`, returning `PreparedChangeset` without publishing |
| `backend/src/sandbox/occ/client.py` | Adds service-backed Phase 03 mode while preserving legacy runtime dispatch mode |
| `backend/src/sandbox/runtime/overlay_shell/capture_to_changeset.py` | Runtime bridge from Phase 02 `UpperChange` values into OCC typed changes |

### Compatibility Updates

| File | Purpose |
| --- | --- |
| `backend/src/sandbox/occ/wire.py` | Carries source/create-only metadata while preserving old runtime codecs |
| `backend/src/sandbox/occ/direct/direct_merge_coordinator.py` | Decodes byte-backed `WriteChange` content for the legacy direct path |
| `backend/src/sandbox/occ/gated/file_change_applier.py` | Decodes byte-backed `WriteChange` content for the legacy gated path |
| `backend/src/sandbox/api/write.py` | Uses the new API write builder |
| `backend/src/sandbox/runtime/bundle.py` | Includes `layer_stack` in the runtime bundle because Phase 03 OCC modules depend on it |
| `backend/src/sandbox/overlay/runner/runtime_bundle.py` | Keeps the Phase 02 overlay-only bundle free of the Phase 03 capture adapter |

### Tests

| Test file | Coverage |
| --- | --- |
| `backend/tests/test_sandbox/test_occ/test_changeset_builders.py` | API and shell builder source tagging plus legacy overlay builder compatibility |
| `backend/tests/test_sandbox/test_occ/test_changeset_routing.py` | tracked/direct/drop/reject grouping and ordered same-path groups |
| `backend/tests/test_sandbox/test_occ/test_gitignore_oracle.py` | Gitignore oracle cache and batch behavior under the new namespace |
| `backend/tests/test_sandbox/test_occ/test_base_hash_inference.py` | Leased snapshot hash inference for shell/API writes and deletes; edit anchors stay hash-free |
| `backend/tests/test_sandbox/test_occ/test_occ_dependency_boundaries.py` | Phase 03 OCC preparation modules do not import overlay or legacy apply coordinators |
| `backend/tests/test_sandbox/test_occ/test_client.py` | Service-backed `OCCClient.apply_changeset` path |
| `backend/tests/test_sandbox/test_runtime/test_capture_to_changeset.py` | Phase 02 upperdir capture to typed OCC changes |
| Existing sandbox tests | Updated for package structure, bundle layout, and API import boundaries |

---

## 3. Behavior Delivered

### Typed Source Changes

`Change` values now carry one of:

```text
api_write
api_edit
shell_capture
```

`WriteChange` stores bytes and can carry either caller-supplied `base_hash` or
`None` for Phase 03 inference. `EditChange` exposes the anchor shape
(`old_text`, `new_text`, `expected_occurrences`) and keeps the legacy `edits`
property so the current live-root gate remains compatible until cutover.

### Routing And Grouping

`ChangeRouter` normalizes paths and returns one `PreparedPathGroup` per ordered
route/path pair:

- tracked workspace paths route to `tracked`
- gitignored paths route to `direct`
- `.git` and descendants route to `drop`
- absolute paths and parent traversal route to `reject`

Direct-only legacy change kinds still route to `direct` without a gitignore
lookup.

### Leased Snapshot Base Hashes

`OccService.apply_changeset(..., snapshot=M0)` hashes tracked write/delete base
content through `LayerStackManager.read_bytes(path, M0)`. The tests verify that
the hash comes from the leased snapshot even if the active manifest advances
before preparation runs.

`EditChange` remains anchor-based and is not converted into a shell-style
full-file CAS write.

### Client And Runtime Bridge

`OCCClient.apply_changeset` now has a service-backed mode for Phase 03 callers:

```text
OCCClient(service=OccService(...)).apply_changeset(...)
-> PreparedChangeset
```

The existing sandbox-id runtime-dispatch mode still returns `ChangesetResult`
for current write/edit/shell callers.

`runtime.overlay_shell.capture_to_changeset` is the only new bridge that imports
both overlay capture objects and OCC builders. OCC preparation modules
themselves remain overlay-free.

### Cleanup Follow-Up

The post-implementation cleanup removed the stale
`sandbox.occ.content.gitignore_oracle` compatibility shim, the unused
`OCCClient.for_layer_stack` helper, the unused `AnyChange` export, the unused
OCC wire upper-change decoder, and a custom bytes/string equality shim in
`WriteChange`. Runtime callers now import `GitignoreOracle` from
`sandbox.occ.routing.gitignore` directly.

Cleanup verification:

```bash
uv run pytest backend/tests/test_sandbox/test_occ/test_wire.py backend/tests/test_sandbox/test_occ/test_changeset_builders.py backend/tests/test_sandbox/test_occ/test_client.py backend/tests/test_sandbox/test_occ/test_gitignore_oracle.py backend/tests/test_sandbox/test_runtime/test_bundle_upload.py -q
```

Result:

```text
45 passed in 0.49s
```

---

## 4. Exit Criteria Mapping

| Phase 03 exit condition | Implementation evidence |
| --- | --- |
| Public OCC changeset surface exists | `OCCClient`, `OccService`, and source-tagged `Change` classes |
| API write/edit builders exist | `build_api_write_change`, `build_api_edit_change`, `build_api_delete_change` |
| Shell capture builder/adapter exists | `build_shell_write_change`, `build_shell_delete_change`, `capture_to_changeset` |
| Path groups prepared | `PreparedPathGroup` and `ChangeRouter.prepare` |
| Gitignore/direct routing exists | `routing.gitignore.GitignoreOracle` and `ChangeRouter._route_change` |
| `.git` paths drop | `RouteDecision.DROP` tests |
| External paths reject | `RouteDecision.REJECT` tests |
| Tracked base hash inferred from leased manifest | `test_tracked_write_without_base_hash_uses_leased_snapshot_hash` |
| Preparation can run without overlay import | `test_phase03_occ_preparation_modules_do_not_import_overlay_or_legacy_apply` |
| Returns `PreparedChangeset` for commit phase | `OccService.apply_changeset` and service-backed `OCCClient` tests |

---

## 5. Verification

Focused Phase 03 tests and lint:

```bash
uv run pytest backend/tests/test_sandbox/test_occ/test_changeset_builders.py backend/tests/test_sandbox/test_occ/test_changeset_routing.py backend/tests/test_sandbox/test_occ/test_gitignore_oracle.py backend/tests/test_sandbox/test_occ/test_base_hash_inference.py backend/tests/test_sandbox/test_occ/test_occ_dependency_boundaries.py backend/tests/test_sandbox/test_occ/test_client.py backend/tests/test_sandbox/test_runtime/test_capture_to_changeset.py -q
```

Result:

```text
30 passed in 0.32s
```

Compatibility OCC/API/runtime slice:

```bash
uv run pytest backend/tests/test_sandbox/test_occ backend/tests/test_sandbox/test_runtime/test_shell_pipeline.py backend/tests/test_sandbox/test_runtime/test_capture_to_changeset.py backend/tests/test_sandbox/test_api_contract.py backend/tests/test_sandbox/test_api/test_write.py backend/tests/test_sandbox/test_api/test_edit.py -q
```

Result:

```text
117 passed in 0.91s
```

Full sandbox suite:

```bash
uv run pytest backend/tests/test_sandbox -q
```

Result:

```text
402 passed in 6.72s
```

Sandbox lint:

```bash
uv run ruff check backend/src/sandbox backend/tests/test_sandbox
```

Result:

```text
All checks passed!
```

Diff whitespace check:

```bash
git diff --check
```

Result: no output.

---

## 6. Deferred Work

| Deferred item | Reason |
| --- | --- |
| Final active-manifest validation | Phase 04 owns commit-time revalidation |
| Layer publish | Phase 04 owns `OccCommitTransaction` and layer publication |
| Shell all-or-nothing tracked conflict policy | Phase 04 owns transaction-level shell atomicity |
| Squash, lease pressure, and GC | Phase 05 owns layer maintenance |
| Removing `occ/orchestrator.py`, `occ/direct/`, and `occ/gated/` | Phase 06 is the documented cutover/removal phase; current runtime callers still depend on them |
| Replacing `wire.py` | Phase 06 final structure removes generic wire helpers after public paths are cut over |
