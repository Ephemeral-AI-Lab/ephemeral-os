# Phase 05 — OCC Mutation Gate

**Status:** implemented
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`
**Current layout:** updated after the handler-per-command refactor and Phase
05.5 OCC backend factory consolidation.

## 1. Task Specification

Host `api.write_file`, `api.edit_file`, `api.read_file`, and `api.shell` on
command-exec-owned runtime handlers as the single host-facing data API surface.

In-workspace mutations flow through one OCC mutation gate:

```text
runtime.handlers.{write,edit,shell}
  -> OCCClient.apply_changeset(...)
  -> OccService.apply_changeset(...)
  -> OccSerialMerger / OccCommitTransaction
  -> layer-stack publish
```

Out-of-workspace operations bypass OCC and operate on the sandbox host
filesystem directly. This matches shell namespace passthrough: a command writing
to `/tmp`, `/home`, or another path outside the bound workspace mutates the host
sandbox filesystem, not the layer-stack manifest.

`occ-server` owns mutation policy through `OCCClient` / `OccService`, but it is
not a host-callable API server. The old test-only `runtime/occ_handlers.py`
structural surface and `OCC_OP_TABLE` have been deleted; tests now assert the
real runtime `OP_TABLE` routing directly.

`layer-stack-server` remains policy-blind storage and lease control.

## 2. In-Scope Runtime Rules

- `api.write_file`, `api.edit_file`, `api.read_file`, and `api.shell` dispatch
  from `backend/src/sandbox/runtime/handlers/`.
- `runtime/handlers/metrics_handler.py` owns `api.layer_metrics`.
- `runtime/occ_server.py` owns the shared `OccBackend` factory/cache.
- `runtime/occ_handlers.py` and `OCC_OP_TABLE` are deleted.
- `runtime/write_edit_handlers.py` is deleted.
- Path classification for write/edit/read lives only in
  `runtime/handlers/_common.py`.
- OCC receives typed changes and does not classify workspace paths.
- In-workspace edit derives final bytes against leased snapshot N before
  calling OCC; OCC receives a `WriteChange`, not an `EditChange`.
- Write/edit/read acquire short-lived snapshot leases from the same
  `LayerStackManager` / `LeaseRegistry` used by shell.
- `api.write_file` and `api.edit_file` remain single-path calls.
- OCC revalidates against the latest active manifest before publish.
- CAS mismatch retry is bounded by `MAX_OCC_CAS_RETRIES = 3`.
- Retry exhaustion returns a conflict result; it never loops indefinitely.

## 3. Out Of Scope

- No host-callable `api.write_file`, `api.edit_file`, or `api.read_file` on
  occ-server.
- No command execution ownership in OCC.
- No layer storage layout ownership in OCC.
- No direct shell-capture call into `OccService`; shell submits through
  `OCCClient`.
- No path classification inside occ-server.
- No separate lease registry for write/edit/read.
- No atomicity across multiple paths in one write/edit API call.

## 4. Main Data Objects

```text
OCCClient
  apply_changeset(typed_changes, snapshot, options, workspace_ref)

OccBackend
  layer_stack: LayerStackClient
  occ_client: OCCClient
  gitignore: SnapshotGitignoreOracle
  manager: LayerStackManager

WriteChange
  path
  final_content
  create_only
  base_hash when OCC-gated

PreparedChangeset
  snapshot identity
  path groups
  atomicity flag
  timings

ChangesetResult
  files
  changed/committed path projection
  conflict status
  timings
  published manifest version
```

## 5. File/Folder Structure

```text
backend/src/sandbox/runtime/
├── handlers/
│   ├── __init__.py
│   ├── _common.py          # classify_path, single-path validation, _services
│   ├── write_handler.py    # api.write_file
│   ├── edit_handler.py     # api.edit_file
│   ├── read_handler.py     # api.read_file
│   ├── shell_handler.py    # api.shell entry
│   └── metrics_handler.py  # api.layer_metrics
├── command_exec_server.py  # shell worker pipeline
├── occ_server.py           # OccBackend factory/cache
├── layer_stack_handlers.py # workspace base/snapshot control
└── server.py               # runtime OP_TABLE registration

backend/src/sandbox/occ/
├── client.py
├── service.py
├── serial_merger.py
├── commit_transaction.py
├── changeset/
├── content/
├── direct/
└── gated/
```

Deleted legacy surfaces:

```text
backend/src/sandbox/runtime/write_edit_handlers.py
backend/src/sandbox/runtime/api_handlers.py
backend/src/sandbox/runtime/occ_handlers.py
api_handlers.write_file / edit_file / read_file
api_handlers._process_commit_gate / _commit_lock
api_handlers.drop_services_cache / _services_cache_clear
OCC_OP_TABLE structural surface
```

## 6. Workflow Demonstration

### 6.1 Write

```text
api.write_file(path, content)
        │
        ▼
runtime.handlers.write_handler.write_file
        │
        ▼
require_workspace_binding(layer_stack_root)
classify_path(path, workspace_root)
        │
        ├── out-of-workspace
        │       └── Path(abs_path).write_text(...)
        │           return changed_paths=[abs_path]
        │
        └── in-workspace
                ├── services = handlers._common._services(root)
                ├── lease = manager.acquire_snapshot_lease(...)
                ├── optional create-only exists check against snapshot N
                ├── change = build_api_write_change(...)
                ├── OCCClient.apply_changeset([change], snapshot=N)
                └── release lease
```

### 6.2 Edit

```text
api.edit_file(path, edits)
        │
        ▼
runtime.handlers.edit_handler.edit_file
        │
        ▼
classify_path(path, workspace_root)
        │
        ├── out-of-workspace
        │       ├── read host FS bytes
        │       ├── validate UTF-8 and anchors
        │       └── write final host FS bytes
        │
        └── in-workspace
                ├── lease snapshot N
                ├── read bytes from layer-stack snapshot N
                ├── validate UTF-8 and anchors against N
                ├── derive final bytes
                ├── submit WriteChange(final bytes) to OCC
                └── release lease
```

### 6.3 Shell

```text
api.shell(command)
        │
        ▼
runtime.handlers.shell_handler.shell
        │
        ▼
command_exec_server.execute_shell_api
        │
        ▼
command_exec_server._execute_shell
        │
        ├── prepare workspace snapshot N
        ├── run command with workspace replacement
        ├── capture upperdir changes
        ├── workspace_changes_to_occ_changes(...)
        ├── OCCClient.apply_changeset(...)
        └── release lease and transient lowerdir
```

### 6.4 Backend Factory

```text
handler / shell worker / metrics
        │
        ▼
occ_server.build_occ_backend(layer_stack_root)
        │
        ├── cache hit: return OccBackend
        └── cache miss:
              manager = get_layer_stack_manager(root)
              layer_stack = LayerStackClient(manager)
              gitignore = SnapshotGitignoreOracle(layer_stack)
              occ_service = OccService(gitignore=..., layer_stack=...)
              occ_client = OCCClient(..., workspace_ref=root)
              return/cache OccBackend(...)
```

## 7. Required Assertions

Server topology:

- `api.write_file`, `api.edit_file`, `api.read_file`, and `api.shell` dispatch
  from `runtime.handlers`.
- `runtime/api_handlers.py` is removed; metrics dispatch lives in
  `runtime.handlers.metrics_handler`.
- `runtime/occ_handlers.py` and `OCC_OP_TABLE` are removed.
- No data API op is registered against `occ_server`.
- Write/edit/shell submit mutations through `OCCClient.apply_changeset`.
- Path classification source is `runtime.handlers._common.classify_path`.

Mutation semantics:

- In-workspace write/edit lease snapshot N before OCC submission.
- Edit validates and derives final bytes against snapshot N.
- OCC revalidates against active manifest M before publish.
- Same-path N-to-M drift returns a conflict, not a silent overwrite.
- Create-only write rejects if path exists in snapshot N.
- CAS mismatch retry is bounded.

Out-of-workspace semantics:

- Out-of-workspace write/edit/read use host FS and do not mutate the manifest.
- Symlink resolution happens before classification.
- Workspace-anchored `..` escape is a hard error.

## 8. Verification

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
