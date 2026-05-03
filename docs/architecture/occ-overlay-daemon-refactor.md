# OCC + Overlay + Daemon Refactor Plan

**Status:** Draft, awaiting execution
**Author:** session 2026-05-03
**Scope:** ~7K LoC, ~35 files across `backend/src/sandbox/code_intelligence/`, `backend/src/sandbox/api/`, `backend/src/sandbox/daemon/` (new), and tests under `backend/tests/test_sandbox/`.
**Companion doc:** `plugins-refactor.md` covers the query-surface replacement (direct LSP plugin tools). Both refactors land together; query-side deletions are listed here for completeness but executed under the plugins plan.

## 0. Motivation

Today `sandbox/code_intelligence/` is an over-broad umbrella that bundles two unrelated guardrails plus a query surface:

1. **Write/edit guardrail** — OCC arbiter, content manager, write coordinator, edit history ledger.
2. **Command-execution guardrail** — overlay, command committer, process exec.
3. ~~Code intelligence queries~~ — moved out under `plugins-refactor.md`.

The two guardrails serve different chokepoints (file edits vs sandbox cmd execution) and deserve to be peers, not siblings under a misleading `code_intelligence/` parent. The umbrella name is misleading once queries leave.

## 0.1 Pre-step: collapse `move_file` / `remove_file` into shell

Before the OCC/Overlay/daemon move starts, delete the two dedicated tools (`tools/sandbox_toolkit/move_file.py`, `tools/sandbox_toolkit/remove_file.py`) and route those operations through `svc.cmd` (`mv`, `rm`). The overlay commit path already funnels every non-gitignored upperdir change through OCC via `OverlayCommandCommitter`, so audit/ledger coverage is preserved.

Doing this *before* the package move keeps OCC's external surface (§2.1) from inheriting verbs we're about to delete, and removes a layer of API plumbing (`AuditedSandboxApi.{move,remove}_file`, daemon RPC handlers, `MoveSpec`-batching code in `mutation_service`) that the refactor would otherwise have to relocate.

**Files deleted in pre-step:**

- `backend/src/tools/sandbox_toolkit/move_file.py`
- `backend/src/tools/sandbox_toolkit/remove_file.py`

**Call sites and downstream code to remove or update in the same change set:**

- `tools/sandbox_toolkit/registry.py` — drop the two imports and registrations.
- `tools/sandbox_toolkit/shell.py:174` and `tools/sandbox_toolkit/_shell_prehooks.py:63` — update guidance strings (no longer steer agents to `remove_file` / `move_file`).
- `tools/submission/hooks/request_complex_task_before_edit_gate.py:19-20` — drop the two tool names from the gate's covered set, or expand the gate to cover `shell` if equivalent coverage is desired.
- `agents/helper_agent/resolver/agent.md`, `agents/main_agent/entry_executor/agent.md`, `agents/main_agent/generator/executor/agent.md` — strip `remove_file` / `move_file` from each agent's tool list.
- `engine/testing/eval_agent.py:384-385` — strip the two tool names from the eval allowlist.
- `sandbox/api/audited_sandbox_api.py:134-162` — delete `remove_file` / `move_file` methods.
- `sandbox/api/sandbox_api.py:49-53` — drop the corresponding protocol methods.
- `sandbox/api/audit.py` — delete `submit_remove_request` / `submit_move_request` and the `RemoveFileRequest` / `MoveFileRequest` / `RemoveFileResult` / `MoveFileResult` models in `sandbox/api/models.py` if no other caller remains.
- `sandbox/code_intelligence/service.py:270-277` — delete `move_file` (and `delete_file` if unused).
- `sandbox/code_intelligence/mutations/mutation_service.py:282-334` — delete `move_file`; remove `op == "move"` / `op == "delete"` branches in `_commit_specs_direct` once verified unused.
- `sandbox/code_intelligence/backends/{protocol.py:88, in_process.py:286-293}` — drop `move_file` from backend protocol + impl.
- `sandbox/code_intelligence/daemon/handlers.py:337-398` — delete `handle_move_file` and remove `"move_file"` from the dispatch table; same for `delete_file` if present.
- `sandbox/code_intelligence/daemon/client.py:414-422` — delete the daemon-client `move_file` / `delete_file` shims.
- `sandbox/code_intelligence/core/types.py:169` — delete `MoveSpec` once mutation_service no longer references it.

**Behavioral consequences (accepted, not mitigated):**

- Agents lose the structured `dst_exists | not_found | aborted_version | aborted_overlap | aborted_lock` enum and read shell stderr / `audit_conflict_reason` instead.
- `mv` clobbers by default; agents must use `mv -n` if non-overwrite is desired.
- `rm -rf` of folders is the agent's responsibility — no `is_folder=True` typed switch.
- Moves/removes of gitignored paths stop hitting OCC (overlay direct-merges gitignored writes); ledger sees only gitinclude-tracked paths.
- Per-op cost rises from ~OCC-only to ~overlay+commit (~1.1s end-to-end vs ~0.65s commit-only).

These are the trade-offs we are choosing in exchange for OCC surface reduction and one fewer tool family.

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

External API: every file edit goes through `OCC.apply_edit(...)`, `OCC.write_file(...)`, `OCC.commit_*(...)`, `OCC.undo_last_edit(...)`. Move and delete verbs are removed from the external surface (see §0.1) — `mv` / `rm` flow through `svc.cmd` and commit via the overlay path. Internally, overlay commits still produce `OperationChange` rows with `delete=True` consumed by `WriteCoordinator`; that is not a public OCC method.

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

- `sandbox/lifecycle/workspace.py` — uses `service.symbol_index`, `service.lsp_client`, etc. (Replaced with OCC + direct plugin-tool lookup wired per `plugins-refactor.md`.)
- `sandbox/api/code_intelligence_api.py` — DELETE
- `sandbox/api/code_intelligence_impl.py` — DELETE
- `sandbox/api/models.py` — strip query types
- `sandbox/api/audit.py` — references mutations module → route through OCC
- `backend/tests/test_sandbox/test_code_intelligence/*` — relocate or delete
- `backend/tests/test_sandbox/test_daemon_*.py` — update for new handler split

## 5. Sequenced execution

This plan picks up after `plugins-refactor.md` step 0 proves sandbox-hosted
basedpyright connectivity and steps 1–5 author/smoke-test the direct LSP plugin
tools, so that `lifecycle/workspace.py` can swap to plugin-tool lookup in one
pass without an intermediate broken state.

```
0. Pre-step: collapse move_file / remove_file into shell (per §0.1)
   - Delete tools/sandbox_toolkit/{move_file.py, remove_file.py}
   - Update tools/sandbox_toolkit/registry.py, shell.py, _shell_prehooks.py
   - Update tools/submission/hooks/request_complex_task_before_edit_gate.py
   - Strip remove_file / move_file from agent.md files and engine/testing/eval_agent.py
   - Delete AuditedSandboxApi.{move,remove}_file + the SandboxApi protocol pair
   - Delete audit.submit_{move,remove}_request and unreferenced request/result models
   - Delete service.move_file, mutation_service.move_file, MoveSpec, backends move_file,
     daemon handle_move_file, daemon client move_file (and delete_file equivalents
     if no internal caller remains)
   - make test + ruff check; iterate to green before starting step 1

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
   - sandbox/lifecycle/workspace.py: replace service.symbol_index/lsp_client refs with OCC + plugin-tool lookup
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
| `lifecycle/workspace.py` rewrite blocked on plugin-tool lookup not being ready | Sequencing: plugin-tool half lands first; this plan starts at step 1 only after plugin smoke test passes. |

## 7. Out of scope

- Anything plugin-related (see `plugins-refactor.md`).
- Multi-daemon-process topologies (still one daemon per sandbox).
- OCC/Overlay sharing a base `Chokepoint` interface (deferred — duck-typed peers for v1).

## 8. Open questions deferred to execution

- Exact location of `sandbox/_paths.py` (root of `sandbox/` vs a tiny `sandbox/util/` package).
- Whether `OCC` and `Overlay` should share a base `Chokepoint` interface or remain duck-typed peers.
- Whether the daemon's `handlers/` package is a hard split or just two files in `handlers/`.

These do not change the plan shape; resolve in the relevant step.
