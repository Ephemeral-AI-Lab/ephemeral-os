# Phase 05 Implementation Report — OCC Mutation Gate

**Date:** 2026-05-07
**Plan:** `three-server-phase-05-occ-mutation-gate.md`
**Status:** implemented and unit-verified
**Current layout:** includes later handler-per-command cleanup and Phase 05.5
OCC backend factory consolidation.

## Summary

Phase 05 moved `api.write_file`, `api.edit_file`, `api.read_file`, and
`api.shell` onto **command-exec** as the single host-facing data API surface.
In-workspace mutations flow through `OCCClient.apply_changeset`; out-of-workspace
operations bypass OCC and write/read the sandbox host filesystem directly,
matching shell namespace passthrough behavior.

`occ-server` remains a logical Python module in the existing runtime daemon, not
a host-callable API. The old test-only `runtime/occ_handlers.py` /
`OCC_OP_TABLE` surface has been deleted; tests pin the real runtime `OP_TABLE`
routing instead. No host-callable `api.write_*`, `api.edit_*`, or `api.read_*`
endpoints live on occ-server.

The host-side wrapper API under `sandbox.api.tool.{write,edit,read,shell}.py`
keeps the same JSON envelope and response shape.

## Current Runtime Files

| File | Role |
|---|---|
| `backend/src/sandbox/runtime/handlers/_common.py` | Shared classifier, single-path validation, result projection helpers, and `_services(layer_stack_root)` access to the shared `OccBackend`. |
| `backend/src/sandbox/runtime/handlers/write_handler.py` | `api.write_file` dispatch; in-workspace OCC path plus out-of-workspace direct-FS path. |
| `backend/src/sandbox/runtime/handlers/edit_handler.py` | `api.edit_file` dispatch; derives final bytes against snapshot N and submits a `WriteChange` to OCC. |
| `backend/src/sandbox/runtime/handlers/read_handler.py` | `api.read_file` dispatch; reads in-workspace bytes from layer-stack snapshots and out-of-workspace bytes from host FS. |
| `backend/src/sandbox/runtime/handlers/shell_handler.py` | `api.shell` entrypoint; delegates shell worker work to `command_exec_server`. |
| `backend/src/sandbox/runtime/handlers/metrics_handler.py` | `api.layer_metrics` diagnostic dispatch. |
| `backend/src/sandbox/runtime/command_exec_server.py` | Shell workspace replacement, upperdir capture, capture-to-OCC apply, and shell result projection. |
| `backend/src/sandbox/runtime/occ_server.py` | Shared `OccBackend` factory/cache used by handlers, shell, and metrics. |
| `backend/src/sandbox/runtime/server.py` | Registers public runtime ops from `runtime.handlers`. |

Deleted or intentionally absent:

```text
backend/src/sandbox/runtime/write_edit_handlers.py
backend/src/sandbox/runtime/api_handlers.py
backend/src/sandbox/runtime/occ_handlers.py
api_handlers.write_file / edit_file / read_file
api_handlers._process_commit_gate / _commit_lock
api_handlers.drop_services_cache / _services_cache_clear
command_exec_server.drop_services_cache
handlers._common.drop_services_cache
OCC_OP_TABLE structural surface
```

## Files Deleted In This Cleanup

| File | Reason |
|---|---|
| `backend/src/sandbox/runtime/write_edit_handlers.py` | Compatibility re-export after the handler-per-command split. Runtime registration and tests now use `runtime.handlers.*` directly. |
| `backend/src/sandbox/runtime/api_handlers.py` | Metrics-only legacy module after the data handlers moved to `runtime.handlers`; `api.layer_metrics` now lives beside the other public runtime handlers. |
| `backend/src/sandbox/runtime/occ_handlers.py` | Test-only structural OCC shim. The live runtime table is now the source of truth for routing assertions. |

Earlier Phase 05 also deleted stale tests that imported
`api_handlers._process_commit_gate` and the old manual prepare/commit split.
The relevant behavior is now covered by the OCC and command-exec test suites.

## Exit Criteria

### Server Topology

| Criterion | Current result |
|---|---|
| Data APIs dispatch from command-exec-owned handlers, not occ-server | `server.OP_TABLE["api.write_file"] -> handlers.write_handler.write_file`, same for edit/read/shell. |
| `runtime/api_handlers.py` is removed | Covered by `test_legacy_api_handlers_module_removed`; `api.layer_metrics` dispatches through `handlers.metrics_handler`. |
| `runtime/occ_handlers.py` is removed | Covered by `test_legacy_occ_handlers_module_removed`. |
| In-workspace write/edit/shell reach OCC via `OCCClient.apply_changeset` | Covered by command-exec and OCC mutation-gate tests. |
| Shell, write, edit, and read share the same `LayerStackManager` / lease registry | Covered by `test_write_edit_read_share_lease_registry_with_shell`. |
| Path classification is not in occ-server | Covered by source-scan tests on `occ_server.py`. |
| `api.write_file` and `api.edit_file` carry exactly one path | Covered by single-path contract tests. |

### OCC Mutation Gate

| Criterion | Current result |
|---|---|
| In-workspace edit validates existence, UTF-8, anchors, and occurrence counts against snapshot N | Covered by `test_edit_snapshot_byte_derivation.py`. |
| Edit submits final bytes as `WriteChange`; OCC does not re-derive edit bytes against active manifest M | Covered by `test_in_workspace_edit_submits_write_change_with_derived_bytes`. |
| Per-call lease covers prepare to publish | Covered by `test_in_workspace_write_pins_lease_then_releases`. |
| CAS mismatch retry is bounded by `MAX_OCC_CAS_RETRIES = 3` | Covered by `test_cas_retry_exhaustion_returns_conflict_result`. |
| Same-path M>N conflict does not silently overwrite intervening bytes | Covered by `test_in_workspace_edit_same_path_M_gt_N_surfaces_hard_conflict`. |
| Create-only in-workspace write rejects if path exists in snapshot N | Covered by `test_in_workspace_create_only_rejects_existing_path`. |

### Out-Of-Workspace Passthrough

| Criterion | Current result |
|---|---|
| `/tmp` writes bypass OCC and leave the manifest unchanged | Covered by `test_out_of_workspace_write_lands_on_host_fs`. |
| Out-of-workspace edit/read operate on host FS bytes | Covered by out-of-workspace passthrough tests. |
| Symlinks resolving outside the workspace classify as out-of-workspace | Covered by classifier predicate tests. |

## Interpretation Decisions

1. **occ-server is not a host-callable mutation API.** Tests assert the real
   runtime `OP_TABLE` data routes and the absence of the old `occ_handlers`
   structural shim instead of keeping a fake OCC dispatch table in production.
2. **Command-exec owns path classification.** `handlers._common.classify_path`
   is the single classifier for write/edit/read. OCC receives typed changes and
   does not decide whether a path is inside the workspace.
3. **Edit derives bytes before OCC.** OCC validates and publishes final bytes;
   it does not apply search/replace semantics against a later active manifest.
4. **The older path-bucket commit gate is gone.** `OccSerialMerger` now owns
   serialization, disjoint-batch coalescing, and bounded CAS retry behavior.
5. **Phase 05.5 centralized backend construction.** All command-exec handlers,
   shell worker code, and metrics read the same cached `OccBackend`.

## Verification

Historical Phase 05 verification, before the later deletion of the compatibility
handler modules:

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
# 359 passed, 1 skipped, 1 warning

.venv/bin/ruff check backend/src/sandbox backend/tests/unit_test/test_sandbox
# All checks passed

.venv/bin/mypy --config-file backend/mypy.ini <then-current Phase 05 runtime files>
# Success: no issues found
```

Current cleanup verification should use the updated handler paths:

```bash
.venv/bin/pytest \
  backend/tests/unit_test/test_sandbox/test_command_exec \
  backend/tests/unit_test/test_sandbox/test_occ/test_mutation_gate.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_daemon.py \
  backend/tests/unit_test/test_sandbox/test_layer_stack/test_workspace_binding.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py \
  -q

.venv/bin/ruff check \
  backend/src/sandbox/runtime \
  backend/src/sandbox/occ/runtime_ops.py \
  backend/tests/unit_test/test_sandbox/test_command_exec \
  backend/tests/unit_test/test_sandbox/test_occ/test_mutation_gate.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_daemon.py \
  backend/tests/unit_test/test_sandbox/test_layer_stack/test_workspace_binding.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py

.venv/bin/mypy --config-file backend/mypy.ini \
  backend/src/sandbox/runtime/handlers \
  backend/src/sandbox/runtime/command_exec_server.py \
  backend/src/sandbox/runtime/occ_server.py \
  backend/src/sandbox/occ/runtime_ops.py
```

## Open Items

- **Live-e2e gate not covered by this cleanup.** The Daytona-backed
  `test_codegen_race.py` gate remains separate verification infrastructure.
- **Phase 06 supervision transport docs may still mention the pre-cleanup
  handler names.** Treat this report and the current code as authoritative for
  the Phase 05 data-operation topology.
