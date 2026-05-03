# OCC + Overlay + Daemon Refactor Plan

**Status:** Draft, awaiting execution
**Author:** session 2026-05-03
**Scope:** ~7K LoC, ~35 files across `backend/src/sandbox/code_intelligence/`, `backend/src/sandbox/api/`, `backend/src/sandbox/daemon/` (new), and tests under `backend/tests/test_sandbox/`.
**Companion doc:** `plugins-refactor.md` covers the query-surface replacement (plugin host + basedpyright). Both refactors land together; query-side deletions are listed here for completeness but executed under the plugins plan.

## 0. Motivation

Today `sandbox/code_intelligence/` is an over-broad umbrella that bundles two unrelated guardrails plus a query surface:

1. **Write/edit guardrail** — OCC arbiter, content manager, write coordinator, edit history ledger.
2. **Command-execution guardrail** — overlay, command committer, process exec.
3. ~~Code intelligence queries~~ — moved out under `plugins-refactor.md`.

The two guardrails serve different chokepoints (file edits vs sandbox cmd execution) and deserve to be peers, not siblings under a misleading `code_intelligence/` parent. The umbrella name is misleading once queries leave.

## 1. End-state architecture

Two independent sandbox-level concerns (plus the daemon process they share):

```
backend/src/
└── sandbox/
    ├── occ/        # was code_intelligence/mutations + ledger
    ├── overlay/    # was code_intelligence/overlay
    └── daemon/     # was code_intelligence/daemon, scope-narrowed
```

`sandbox/code_intelligence/` ceases to exist.

### 1.1 Naming decisions

- **No `guardrail/` umbrella.** OCC and Overlay are two separate modules.
- The OCC/Overlay split is the contract: edits go through OCC, shell goes through Overlay. No third path.

### 1.2 OCC chokepoint

Every file edit converges on a single OCC class. `mutation_service.py`, `arbiter.py`, `content_manager.py`, `patcher.py`, `time_machine.py`, and `write_coordinator/` collapse into OCC internals. External callers see one entry point.

### 1.3 Overlay chokepoint

Every `service.cmd()` routes through `sandbox/overlay/`. Existing overlay logic relocates with minimal change — the chokepoint is already in place; the move just makes it visible.

## 2. Sandbox-side modules

### 2.1 `sandbox/occ/`

Single OCC class is the chokepoint. Internals:

```
sandbox/occ/
├── __init__.py
├── occ.py                         # OCC class (the chokepoint) — wraps everything below
├── arbiter.py                     # was mutations/arbiter.py
├── content_manager.py             # was mutations/content_manager.py
├── patcher.py                     # was mutations/patcher.py
├── time_machine.py                # was mutations/time_machine.py
├── write_coordinator/             # unchanged structure, relocated
├── ledger_store.py                # was daemon/ledger_store.py — edit history
├── types.py                       # was core/types.py (EditSpec, WriteSpec, MoveSpec, OperationResult)
├── hashing.py                     # was core/hashing.py
├── registry.py                    # get_occ(sandbox_id) → OCC
├── telemetry.py                   # OCC-specific portion of code_intelligence/telemetry.py
└── backends/
    ├── __init__.py
    ├── protocol.py
    ├── in_process.py              # OCC running in current process
    └── daemon.py                  # OCC routed through sandbox daemon RPC
```

External API: every file edit goes through `OCC.apply_edit(...)`, `OCC.write_file(...)`, `OCC.delete_file(...)`, `OCC.move_file(...)`, `OCC.commit_*(...)`, `OCC.undo_last_edit(...)`. No other surface accepts edit specs.

### 2.2 `sandbox/overlay/`

Mostly relocation:

```
sandbox/overlay/
├── __init__.py
├── overlay.py                     # Overlay class (chokepoint) — every cmd goes here
├── auditor.py                     # was overlay/auditor.py
├── command_committer.py
├── command_executor.py
├── config.py
├── daemon_local.py
├── process_exec.py
├── results.py
├── run.py
├── support.py
├── types.py
├── runtime/                       # unchanged
├── registry.py                    # get_overlay(sandbox_id) → Overlay
├── telemetry.py                   # overlay-specific portion of code_intelligence/telemetry.py
└── backends/
    ├── protocol.py
    ├── in_process.py
    └── daemon.py
```

External API: every sandbox cmd goes through `Overlay.cmd(sandbox, command, **kwargs)`. No other surface invokes the sandbox shell.

### 2.3 `sandbox/daemon/`

The daemon process moves out from under `code_intelligence/` and becomes a sandbox-level concern. Single daemon process per sandbox; `handlers.py` splits into:

```
sandbox/daemon/
├── __init__.py
├── __main__.py
├── server.py
├── client.py
├── launcher.py
├── guard.py
├── state.py
├── storage.py                     # daemon-process storage primitives (NOT the symbol index)
├── paths.py
├── protocol.py
├── wire.py                        # symbol-query wire types DELETED
├── handlers/
│   ├── __init__.py
│   ├── edit.py                    # was handlers.py edit-related portion → calls into occ.*
│   └── cmd.py                     # was handlers.py cmd-related portion → calls into overlay.*
```

DELETED from daemon: `index_store.py`, all symbol-query RPC handlers, all symbol-related wire types. (See `plugins-refactor.md` for the query-side replacement.)

### 2.4 Shared path utilities

`code_intelligence/core/path_utils.py` and `core/constants.py` → `sandbox/_paths.py` (single util module shared by occ + overlay + daemon). Anything occ-specific lives in `occ/`; anything overlay-specific in `overlay/`.

## 3. Deletions

### 3.1 Code

- `sandbox/code_intelligence/service.py` (CodeIntelligenceService facade — replaced by `OCC` + `Overlay` separately)
- `sandbox/code_intelligence/registry.py` (replaced by `occ/registry.py` + `overlay/registry.py`)
- `sandbox/code_intelligence/__init__.py`, `telemetry.py`, `backends/` — all relocated or deleted
- `sandbox/code_intelligence/` (the directory itself, after everything inside has moved or been deleted)
- Query-side deletions (`indexing/`, `language_server/`, `daemon/index_store.py`) — owned by `plugins-refactor.md`.

### 3.2 API surface

- `sandbox/api/code_intelligence_api.py` (entire file)
- `sandbox/api/code_intelligence_impl.py` (entire file)
- Query-related types in `sandbox/api/models.py` (`SymbolInfo`, `ReferenceInfo`, `HoverResult`, `Diagnostic`, etc. — relocated only if still referenced; otherwise deleted)
- New: `sandbox/api/occ_api.py` and `sandbox/api/overlay_api.py` if external HTTP/RPC surface is needed

### 3.3 Tests

- `backend/tests/test_sandbox/test_code_intelligence/*` — relocate to `test_occ/` and `test_overlay/`, or delete
- `backend/tests/test_sandbox/test_daemon_*.py` — update for new handler split
- Indexing/query test deletions are owned by `plugins-refactor.md`.

### 3.4 No backward-compat shims

Per agreed scope: every external call site is rewritten in the same change set. No re-export shims, no deprecation wrappers.

## 4. External call sites to rewrite

Found via grep:

- `sandbox/lifecycle/workspace.py` — uses `service.symbol_index`, `service.lsp_client`, etc. (Replaced with OCC + plugin lookup; plugin lookup is wired per `plugins-refactor.md`.)
- `sandbox/api/code_intelligence_api.py` — DELETE
- `sandbox/api/code_intelligence_impl.py` — DELETE
- `sandbox/api/models.py` — strip query types
- `sandbox/api/audit.py` — references mutations module → route through OCC
- `backend/tests/test_sandbox/test_code_intelligence/*` — relocate or delete
- `backend/tests/test_sandbox/test_daemon_*.py` — update for new handler split

## 5. Sequenced execution

This plan picks up after `plugins-refactor.md` steps 1–5 (plugin host + basedpyright authored, smoke-tested) so that `lifecycle/workspace.py` can swap to plugin lookup in one pass without an intermediate broken state.

```
1. Move sandbox/code_intelligence/mutations/ → sandbox/occ/
   - Collapse arbiter + patcher + content_manager + mutation_service into OCC class
   - Keep write_coordinator/, time_machine.py, edit_history_ledger.py as internals

2. Move sandbox/code_intelligence/overlay/ → sandbox/overlay/
   - Verify Overlay.cmd is the only entry; no other module invokes shell

3. Move sandbox/code_intelligence/core/ types
   - EditSpec/WriteSpec/MoveSpec/OperationResult → sandbox/occ/types.py
   - Path normalizers → sandbox/_paths.py
   - hashing → sandbox/occ/hashing.py

4. Move sandbox/code_intelligence/daemon/ → sandbox/daemon/
   - Split handlers.py into handlers/edit.py + handlers/cmd.py
   - DELETE: index_store.py, symbol-query handlers, symbol wire types
   - ledger_store.py moves to sandbox/occ/ledger_store.py

5. Move backends
   - occ-related backend logic → sandbox/occ/backends/{protocol.py, in_process.py, daemon.py}
   - overlay-related backend logic → sandbox/overlay/backends/...
   - DELETE old code_intelligence/backends/

6. New registries
   - sandbox/occ/registry.py: get_occ(sandbox_id), get_occ_if_exists(...), dispose_occ(...)
   - sandbox/overlay/registry.py: get_overlay(sandbox_id), get_overlay_if_exists(...), dispose_overlay(...)
   - Old code_intelligence/registry.py and service.py DELETED

7. Mass deletions
   - api/code_intelligence_api.py + code_intelligence_impl.py
   - Query types from api/models.py
   - Tests targeting deleted surface (coordinated with plugins-refactor.md §4)

8. Rewrite call sites — no shims
   - sandbox/lifecycle/workspace.py: replace service.symbol_index/lsp_client refs with OCC + plugin lookup
   - api/audit.py: route through OCC
   - Any remaining tools/* references: rewrite or delete

9. Clean up tests
   - Relocate OCC tests to backend/tests/test_sandbox/test_occ/
   - Relocate overlay tests to backend/tests/test_sandbox/test_overlay/
   - Delete indexing/query tests (per plugins-refactor.md)

10. make test + ruff check; iterate to green

11. Documentation
    - docs/architecture/code-intelligence-in-sandbox-daemon/ → docs/architecture/occ-overlay/
    - Rewrite phase-08 implementation report to reflect new architecture

12. Final verification: code_intelligence/ directory empty → `git rm -r` it.
```

## 6. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Daemon handler split breaks RPC compatibility | Same wire protocol, only delivery routing changes. Update tests for new dispatch surface. |
| External callers depending on mutation/overlay internals | All call sites enumerated in §4; rewritten in same change set. |
| Lost edit history during move | `ledger_store.py` relocates as-is; no schema change. |
| Tests fail to delete cleanly | Each test file inspected; deletion is line-item, not bulk. |
| `lifecycle/workspace.py` rewrite blocked on plugin lookup not being ready | Sequencing: plugin half lands first; this plan starts at step 1 only after plugin smoke test passes. |

## 7. Out of scope

- Anything plugin-related (see `plugins-refactor.md`).
- Multi-daemon-process topologies (still one daemon per sandbox).
- OCC/Overlay sharing a base `Chokepoint` interface (deferred — duck-typed peers for v1).

## 8. Open questions deferred to execution

- Exact location of `sandbox/_paths.py` (root of `sandbox/` vs a tiny `sandbox/util/` package).
- Whether `OCC` and `Overlay` should share a base `Chokepoint` interface or remain duck-typed peers.
- Whether the daemon's `handlers/` package is a hard split or just two files in `handlers/`.

These do not change the plan shape; resolve in the relevant step.
