# Phase 05.5 — OCC Backend Factory Consolidation

**Status:** implemented and cleaned up
**Predecessor:** `three-server-phase-05-occ-mutation-gate.md` (shipped 2026-05-07)
**Current code shape:** includes the post-Phase-05 handler-per-command refactor;
`runtime/write_edit_handlers.py` has been deleted.
`runtime/api_handlers.py` has also been deleted; `api.layer_metrics` now lives
beside the public verb handlers.

## 1. Goal

Collapse the repeated runtime construction of:

```text
LayerStackManager
  -> LayerStackClient
  -> SnapshotGitignoreOracle
  -> OccService
  -> OCCClient
```

into one cached backend factory owned by `runtime/occ_server.py`.

This keeps one OCC backend tuple per `layer_stack_root` and removes the older
fan-out where each peer had its own `_SERVICE_CACHE` / `drop_services_cache`
plumbing.

## 2. Current Source Of Truth

```text
backend/src/sandbox/runtime/
├── occ_server.py
│   ├── OccBackend(layer_stack, occ_client, gitignore, manager)
│   ├── build_occ_backend(layer_stack_root)
│   ├── drop_backend_cache(layer_stack_root)
│   └── _backend_cache_clear()          # test helper
├── handlers/
│   ├── _common.py                      # classify_path + _services
│   ├── write_handler.py                # api.write_file
│   ├── edit_handler.py                 # api.edit_file
│   ├── read_handler.py                 # api.read_file
│   ├── shell_handler.py                # api.shell entry, delegates shell work
│   └── metrics_handler.py              # api.layer_metrics
├── command_exec_server.py              # shell worker pipeline; _services
│                                      # delegates to occ_server
├── layer_stack_handlers.py             # drops occ_server cache on reset
└── server.py                           # registers api.* ops from handlers/*
```

Deleted cleanup surface:

```text
backend/src/sandbox/runtime/write_edit_handlers.py
backend/src/sandbox/runtime/api_handlers.py
backend/src/sandbox/runtime/occ_handlers.py
api_handlers.drop_services_cache
api_handlers._services_cache_clear
command_exec_server.drop_services_cache
handlers._common.drop_services_cache
OCC_OP_TABLE structural surface
```

## 3. Backend Object

```text
OccBackend
├── layer_stack: LayerStackClient
├── occ_client: OCCClient
├── gitignore: SnapshotGitignoreOracle
└── manager: LayerStackManager
```

`handlers._common._services(layer_stack_root)` returns this dataclass directly
for write/edit/read handlers.

`command_exec_server._services(args)` still returns the shell worker's legacy
4-tuple shape:

```text
(layer_stack, occ_client, gitignore, storage_root)
```

Internally, that tuple is derived from the same cached `OccBackend`.

`handlers.metrics_handler.layer_metrics` reads
`occ_server.build_occ_backend(...).manager` directly; no local cache remains in
the metrics path.

## 4. Runtime Workflow

### 4.1 Backend Acquisition

```text
write_handler / edit_handler / read_handler / command_exec_server / metrics_handler
        │
        ▼
occ_server.build_occ_backend(layer_stack_root)
        │
   ┌────┴───────────────────────────┐
   │ cache hit: return OccBackend   │
   │ cache miss:                    │
   │   manager = get_layer_stack_manager(root)
   │   layer_stack = LayerStackClient(manager)
   │   gitignore = SnapshotGitignoreOracle(layer_stack)
   │   occ_service = OccService(gitignore=..., layer_stack=...)
   │   occ_client = OCCClient(
   │       occ_service,
   │       binding_reader=RuntimeWorkspaceBindingReader(),
   │       workspace_ref=root,
   │   )
   │   cache[root] = OccBackend(...)
   └────────────────────────────────┘
```

### 4.2 Cache Drop

```text
layer_stack_handlers.build_workspace_base(reset=True)
        │
        ▼
_drop_peer_runtime_caches(layer_stack_root)
        │
        ▼
occ_server.drop_backend_cache(layer_stack_root)
```

Tests that need isolation call `occ_server._backend_cache_clear()`.

### 4.3 Empty Workspace Read

```text
read_handler.read_file(path)
        │
        ▼
require_workspace_binding(layer_stack_root)
        │
        ▼
read_handler._read_in_workspace(layer_path)
        │
        ▼
services = handlers._common._services(root)
lease = manager.acquire_snapshot_lease(...)
content, exists = layer_stack.read_text(layer_path, lease.manifest)
release_lease(...)
        │
        ▼
{"success": True, "exists": exists, "content": content, ...}
```

A bound but empty manifest now reads as `exists=False, content=""`; it does not
raise `WorkspaceBindingError` after the outer binding check has passed.

## 5. Boundary Rules

- `occ_server.py` owns backend construction and caching only.
- `occ_server.py` does not classify workspace paths.
- `handlers._common.classify_path` is the single classifier for write/edit/read.
- `runtime/occ_handlers.py` and `OCC_OP_TABLE` remain deleted; tests assert
  real runtime routing instead of a fake OCC dispatch table.
- Host-callable data operations are registered from `runtime.handlers`, not from
  `occ_server` and not from a compatibility shim.

## 6. Exit Criteria

| Criterion | Current verifier |
|---|---|
| One backend cache per `layer_stack_root` | `test_occ/test_mutation_gate.py::test_single_occ_backend_cache_per_layer_stack_root` |
| No `runtime/write_edit_handlers.py` shim | bundle required-path test excludes it; `rg "write_edit_handlers" backend/src backend/tests` has no live import |
| No `runtime/api_handlers.py` legacy metrics module | bundle required-path test excludes it; `test_legacy_api_handlers_module_removed` pins the removed import |
| No local `_SERVICE_CACHE` in runtime peers | `rg "_SERVICE_CACHE" backend/src/sandbox/runtime` returns no source hits |
| Reset drops the shared backend cache once | `layer_stack_handlers._drop_peer_runtime_caches -> occ_server.drop_backend_cache` |
| Empty bound workspace read returns `exists=False` | `test_layer_stack/test_workspace_binding.py::test_read_file_returns_exists_false_for_empty_manifest` |
| No test-only OCC handler surface | `test_occ/test_mutation_gate.py::test_legacy_occ_handlers_module_removed` |

## 7. Verification

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
