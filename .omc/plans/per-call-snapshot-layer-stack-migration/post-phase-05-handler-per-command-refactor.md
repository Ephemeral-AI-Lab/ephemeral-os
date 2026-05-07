# Post-Phase-05 — Handler-per-Command Refactor (Option A)

**Status:** draft implementation plan
**Predecessor:** `three-server-phase-05-occ-mutation-gate.md` (landed)
**Scope:** Restructure `runtime/` so each public verb (`api.shell`, `api.write_file`, `api.edit_file`, `api.read_file`) has its own handler module under a `runtime/handlers/` package. Worker-level shell scaffolding stays in `runtime/command_exec_server.py`.

## 1. Goal

Replace `runtime/write_edit_handlers.py` (3-verb file) and the `shell()` dispatch entry on `runtime/command_exec_server.py` with one thin handler module per public verb. Shared scaffolding (path classifier, single-path validation, services cache) lives in `runtime/handlers/_common.py`. Net result: the host-facing dispatch surface is four files, one per verb, each ~100 LOC.

Out of scope:
- Changing the host-side `sandbox.api.tool.{shell,write,edit,read}.py` wrappers.
- Changing the OCC architecture (occ_handlers.py / occ_server.py untouched).
- Touching the layer-stack-server, overlay-server, or any non-runtime module.
- Modifying `command_exec/` package internals (workspace mount, capture, namespace helper).

## 2. Target File/Folder Structure

```
backend/src/sandbox/runtime/
├── server.py                     (registers api.* ops, now imports from handlers.*)
├── daemon.py                     (unchanged)
├── async_bridge.py               (unchanged)
├── api_handlers.py               (unchanged — layer_metrics + cascading cache)
├── layer_stack_handlers.py       (cascade peer drops; updated to drop handlers cache)
├── layer_stack_server.py         (unchanged)
├── occ_handlers.py               (unchanged — OCC_OP_TABLE structural surface)
├── occ_server.py                 (unchanged)
├── command_exec_server.py        (TRIMMED — loses public `shell()` dispatch entry;
│                                   keeps _execute_shell, _apply_workspace_capture,
│                                   _command_request, _run_dir, _drop_transient_lowerdir,
│                                   _payload_from_result, _services, _SERVICE_CACHE,
│                                   drop_services_cache, _services_cache_clear,
│                                   _gitignore_timings, _conflict_to_dict)
├── handlers/                     (NEW)
│   ├── __init__.py               (re-exports: shell, write_file, edit_file, read_file)
│   ├── _common.py                (NEW: classify_path, ClassifiedPath, _services,
│   │                              _SERVICE_CACHE, _services_cache_clear,
│   │                              drop_services_cache, _layer_stack_root,
│   │                              _required_single_path, _gitignore_timings,
│   │                              _conflict_to_dict, _project_changeset)
│   ├── shell_handler.py          (NEW: api.shell entry; delegates to
│   │                              command_exec_server._execute_shell + payload formatter)
│   ├── write_handler.py          (NEW: api.write_file with in-workspace OCC +
│   │                              out-of-workspace passthrough branches)
│   ├── edit_handler.py           (NEW: api.edit_file with snapshot-N byte derivation +
│   │                              out-of-workspace direct-FS branches)
│   └── read_handler.py           (NEW: api.read_file with in-workspace SnapshotReader +
│                                   out-of-workspace direct-FS branches)
├── clients/                      (unchanged)
└── overlay_shell/                (unchanged)

DELETED:
└── runtime/write_edit_handlers.py
```

## 3. Handler-by-Handler Surface

### `handlers/_common.py`

```python
# Path classification
classify_path(raw_path: str, workspace_root: str) -> ClassifiedPath
ClassifiedPath  # NamedTuple(classification, abs_path, layer_path)

# Single-path contract
_required_single_path(args: Mapping) -> str
_layer_stack_root(args: Mapping) -> str

# Service composition (shared by write/edit/read handlers)
_Services  # NamedTuple(layer_stack, occ_client, gitignore, manager)
_SERVICE_CACHE: dict[str, _Services]
_services(layer_stack_root: str) -> _Services
_services_cache_clear() -> None
drop_services_cache(layer_stack_root: str) -> None

# Result projection helpers (shared by write/edit only)
_project_changeset(result, *, fallback_path, verb, total_start, gitignore, timings_extra) -> dict
_gitignore_timings(gitignore) -> dict[str, float]
_conflict_to_dict(conflict) -> dict | None
```

### `handlers/shell_handler.py` (~25 LOC)

```python
async def shell(args: dict) -> dict:
    """Thin api.shell entry. Workers live in command_exec_server."""
    from sandbox.runtime import command_exec_server

    layer_stack, occ_client, gitignore, storage_root = command_exec_server._services(args)
    result = await command_exec_server._execute_shell(
        args,
        layer_stack=layer_stack,
        occ_client=occ_client,
        gitignore=gitignore,
        storage_root=storage_root,
    )
    return command_exec_server._payload_from_result(result)
```

(Identical to today's `command_exec_server.shell()` body — this just relocates the dispatch entry.)

### `handlers/write_handler.py` (~120 LOC)

```python
async def write_file(args: dict) -> dict:
    # 1. Validate single-path + layer_stack_root
    # 2. classify_path → in_workspace | out_of_workspace
    # 3. Branch:
    #    - in_workspace: acquire snapshot lease → check create_only against snapshot
    #      → build WriteChange → OCCClient.apply_changeset → release lease
    #    - out_of_workspace: Path(abs).write_text — no lease, no OCC
```

(Body extracted as-is from `write_edit_handlers.write_file` + `_write_in_workspace` + `_write_out_of_workspace`.)

### `handlers/edit_handler.py` (~140 LOC)

```python
async def edit_file(args: dict) -> dict:
    # 1. Validate single-path + parse edits list
    # 2. classify_path
    # 3. Branch:
    #    - in_workspace: acquire lease → SnapshotReader.read_bytes → validate UTF-8 +
    #      anchors against snapshot N → derive final bytes → submit WriteChange to
    #      OCCClient.apply_changeset → release lease
    #    - out_of_workspace: read host bytes → validate UTF-8 + anchors → write back
```

(Body extracted as-is from `write_edit_handlers.edit_file` + `_edit_in_workspace` + `_edit_out_of_workspace` + `_apply_edits`.)

### `handlers/read_handler.py` (~70 LOC)

```python
async def read_file(args: dict) -> dict:
    # 1. Validate single-path + layer_stack_root → require_workspace_binding
    # 2. classify_path
    # 3. Branch:
    #    - in_workspace: lease snapshot N → LayerStackClient.read_text(path, manifest=N)
    #      → release lease
    #    - out_of_workspace: Path(abs).read_text — no lease, no SnapshotReader
```

### `handlers/__init__.py`

```python
from sandbox.runtime.handlers.edit_handler import edit_file
from sandbox.runtime.handlers.read_handler import read_file
from sandbox.runtime.handlers.shell_handler import shell
from sandbox.runtime.handlers.write_handler import write_file

__all__ = ["edit_file", "read_file", "shell", "write_file"]
```

## 4. Migration Steps

| # | Step | Verifier |
|---|---|---|
| 1 | Create `handlers/` package with `__init__.py` (empty re-exports). | `import sandbox.runtime.handlers` succeeds. |
| 2 | Create `handlers/_common.py` by copying classifier predicate + service cache + helpers from `write_edit_handlers.py`. Keep `write_edit_handlers.py` intact for now. | Module imports cleanly; `classify_path` + `_services` callable from new module. |
| 3 | Create `handlers/write_handler.py` — extract `write_file` + `_write_in_workspace` + `_write_out_of_workspace` from `write_edit_handlers.py`, point them at `_common`. | New file imports cleanly; `write_handler.write_file` is callable. |
| 4 | Repeat for `handlers/edit_handler.py` (`edit_file` + edit branches + `_apply_edits`). | Same. |
| 5 | Repeat for `handlers/read_handler.py` (`read_file` + read branches). | Same. |
| 6 | Create `handlers/shell_handler.py` — extract just the `shell()` entry from `command_exec_server.py`; it delegates back into `command_exec_server._execute_shell` + `_payload_from_result` + `_services` (those stay where they are). | `shell_handler.shell` is callable; the workers in `command_exec_server` are untouched. |
| 7 | Update `handlers/__init__.py` to re-export the four entry points. | `from sandbox.runtime.handlers import shell, write_file, edit_file, read_file` works. |
| 8 | Update `runtime/server.py::_load_peer_bootstraps` to import from `handlers.*` instead of `write_edit_handlers` and `command_exec_server.shell`. | `server.OP_TABLE['api.write_file'].__module__` resolves to `handlers.write_handler` etc. |
| 9 | Update `runtime/api_handlers.py::drop_services_cache` and `_services_cache_clear` cascade to call `handlers._common.drop_services_cache` instead of `write_edit_handlers.drop_services_cache`. | `api_handlers.drop_services_cache` cascades correctly. |
| 10 | Update `runtime/layer_stack_handlers.py::_drop_peer_runtime_caches` similarly. | Same. |
| 11 | Delete `runtime/write_edit_handlers.py`. | `import sandbox.runtime.write_edit_handlers` raises ModuleNotFoundError. |
| 12 | Remove `shell()` public dispatch from `command_exec_server.py`; keep `_execute_shell`, `_apply_workspace_capture`, `_payload_from_result`, `_command_request`, `_run_dir`, `_drop_transient_lowerdir`, `_services`, `_SERVICE_CACHE`, `drop_services_cache`, `_services_cache_clear`, `_gitignore_timings`, `_conflict_to_dict`. | `hasattr(command_exec_server, 'shell')` is False; `_execute_shell` callable. |
| 13 | Update tests that import from `write_edit_handlers` to import from `handlers` instead. | Test discovery succeeds; no stale module imports. |
| 14 | Update tests that import `command_exec_server.shell` to import `handlers.shell_handler.shell`. | Same. |
| 15 | Update `tests/test_runtime/test_bundle_upload.py` required-paths list to include the new `handlers/` files. | Bundle test passes. |
| 16 | Run full unit suite + ruff + mypy + import-fence test. | 362 tests pass, ruff green, mypy green, no boundary violations. |

## 5. Test Migration Checklist

| Test file | Required change |
|---|---|
| `test_command_exec/test_write_edit_dispatch.py` | Imports `from sandbox.runtime import write_edit_handlers` → `from sandbox.runtime.handlers import _common, write_handler, edit_handler, read_handler`. References to `write_edit_handlers.write_file`/`edit_file`/`read_file` → `handlers.{write,edit,read}_handler.{write,edit,read}_file`. References to `write_edit_handlers._services` → `handlers._common._services`. References to `write_edit_handlers.classify_path` → `handlers._common.classify_path`. |
| `test_command_exec/test_out_of_workspace_passthrough.py` | Same import updates. |
| `test_command_exec/test_edit_snapshot_byte_derivation.py` | Same import updates. |
| `test_occ/test_mutation_gate.py` | `write_edit_handlers` references → `handlers._common`. |
| `test_occ/test_shell_capture_atomicity.py` | `command_exec_server.shell` → `handlers.shell_handler.shell`. `command_exec_server._services` stays (workers still live there). |
| `test_command_exec/test_capture_to_occ_client.py` | No change — already uses `command_exec_server._execute_shell` directly, which stays put. |
| `test_layer_stack/test_workspace_binding.py` | `write_edit_handlers.read_file` → `handlers.read_handler.read_file`. |
| `test_runtime/test_daemon.py` | `write_edit_handlers._services` → `handlers._common._services`. Cascade-cache test references update. |
| `test_runtime/test_bundle_upload.py` | Required-paths list: drop `runtime/write_edit_handlers.py`; add `runtime/handlers/__init__.py`, `runtime/handlers/_common.py`, `runtime/handlers/shell_handler.py`, `runtime/handlers/write_handler.py`, `runtime/handlers/edit_handler.py`, `runtime/handlers/read_handler.py`. |
| `test_api/test_shell_staleness_telemetry.py` | `write_edit_handlers._services` → `handlers._common._services`. |
| All host-side tool tests (`test_api/test_{write,edit,read,shell}.py`) | No change — they mock `call_runtime_api` and don't import runtime internals. |

## 6. Verification Gate

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
# Expect: 362 passed, 1 skipped (no regressions)

.venv/bin/ruff check backend/src/sandbox backend/tests/unit_test/test_sandbox
# Expect: All checks passed

.venv/bin/mypy --config-file backend/mypy.ini \
  backend/src/sandbox/runtime/handlers/ \
  backend/src/sandbox/runtime/command_exec_server.py \
  backend/src/sandbox/runtime/api_handlers.py
# Expect: Success: no issues found
```

Plus structural probes:

```python
from sandbox.runtime import server
from sandbox.runtime.handlers import shell, write_file, edit_file, read_file
server._load_peer_bootstraps()
assert server.OP_TABLE['api.shell'] is shell
assert server.OP_TABLE['api.write_file'] is write_file
assert server.OP_TABLE['api.edit_file'] is edit_file
assert server.OP_TABLE['api.read_file'] is read_file
# Confirm write_edit_handlers no longer exists
import importlib
try:
    importlib.import_module('sandbox.runtime.write_edit_handlers')
    raise AssertionError('write_edit_handlers should be deleted')
except ModuleNotFoundError:
    pass
# Confirm command_exec_server no longer exposes shell as a public dispatch
from sandbox.runtime import command_exec_server
assert not hasattr(command_exec_server, 'shell')
assert hasattr(command_exec_server, '_execute_shell')  # workers stay
```

## 7. Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| Test imports drift if any test file references `write_edit_handlers` are missed | Low | grep `write_edit_handlers` across the repo before deleting; ensure zero matches. |
| `handlers/_common._services` cache and `command_exec_server._services` cache hold different (LayerStackClient, OCCClient) tuples for the same `layer_stack_root` — both reach the same `LayerStackManager` singleton, so the LeaseRegistry stays unified, but the OCCClient instance is duplicated. | Low | Acceptable — OCCClient is stateless; the only shared state (LayerStackManager + LeaseRegistry + GitignoreOracle) is one instance per root. |
| Bundle test required-paths list goes stale | Low | Update in step 15; verified by `test_bundle_layout_includes_required_paths`. |
| Circular import between `handlers.shell_handler` and `command_exec_server` | Low | Use late import inside the function body (Python pattern already used by `api_handlers.drop_services_cache`). |
| Stop-hook fires (Ralph mode) blocking the work | n/a | Phase 05 ralph already cancelled; this refactor runs in normal mode. |

## 8. Effort Estimate

| Step | Time |
|---|---:|
| Steps 1–7 (create handlers package) | 30 min |
| Steps 8–10 (rewire imports + caches) | 15 min |
| Steps 11–12 (delete `write_edit_handlers.py`, trim `command_exec_server.py`) | 10 min |
| Steps 13–15 (test migrations) | 30 min |
| Step 16 (verification gate) | 10 min |
| **Total** | **~90 min** |

## 9. Out-of-Scope Follow-ups

- Extract `_gitignore_timings` and `_conflict_to_dict` from both `command_exec_server.py` and `handlers/_common.py` into a single shared util once both consumers stabilize. (Currently duplicated; intentionally not deduplicated in this phase to keep the diff surgical.)
- Move OCC create-only enforcement out of `handlers/write_handler.py` into the OCC merge layer (acknowledged debt from Phase 05 §6).
- Consider whether `command_exec_server.py` should be renamed to `shell_workers.py` once `shell()` dispatch moves to `handlers/shell_handler.py` — name now over-claims its scope.

## 10. Why This Is Worth Doing

* **Symmetry**: every public verb has the same handler-file shape; a new contributor finds the entry point by `ls runtime/handlers/`.
* **Reduces top-of-file noise**: each handler ~100 LOC instead of one 480-LOC file mixing three verbs.
* **Sharper boundaries**: `command_exec_server.py` becomes a workers-only module; the handler is the dispatch contract.
* **Cheap to reverse**: if the split adds friction, collapsing back to `write_edit_handlers.py` is a single git revert.

## 11. Why This Could Be a Bad Idea

* **Adds 4 files for ~480 LOC**: the diff cost is real; for a project with one human reader, one file is fine.
* **Doesn't fix any bug or §6 gap**: pure aesthetics.
* **`_common.py` is still a bag-of-helpers**: the duplication with `command_exec_server` (gitignore_timings, conflict_to_dict) is left intact.

If you decide the trade-off doesn't pencil, this plan is a no-op — the Phase 05 implementation is already shippable as-is.
