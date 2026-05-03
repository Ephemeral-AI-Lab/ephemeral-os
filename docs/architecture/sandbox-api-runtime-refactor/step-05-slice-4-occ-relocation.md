# Step 5 — Slice 4 — OCC peer relocation

**Goal.** Move OCC under `sandbox/occ/`; add OCC's `client.py` route point and `setup.sh`; register its setup and handlers with the runtime at import time. Wire `edit_pipeline` (multi-edit OCC apply + atomic commit) and `write_pipeline` (OCC write + commit) end-to-end inside the sandbox. Both are reachable through `runtime/server.py` but **not yet exposed via `sandbox.api`** — that's Slice 6.

**Depends on.** Step 4 / Slice 3.

## Files

### Move
- `backend/src/sandbox/code_intelligence/mutations/` → `backend/src/sandbox/occ/`.
  Do not dump the whole package under `handlers/`: `handlers/` is only the
  server-facing request adapter layer.
- OCC-relevant pieces of `backend/src/sandbox/code_intelligence/core/types.py` → `backend/src/sandbox/occ/types.py`. Non-OCC pieces stay (or migrate under `plugins-refactor.md`).
- `backend/src/sandbox/code_intelligence/core/hashing.py` → `backend/src/sandbox/occ/hashing.py`.
- `backend/src/sandbox/code_intelligence/daemon/ledger_store.py` → `backend/src/sandbox/occ/ledger_store.py`.

### Add
- `backend/src/sandbox/occ/__init__.py`
- `backend/src/sandbox/occ/client.py` — `OCCClient`, the host-side typed route for every OCC server request. It serializes the request, invokes `runtime/server.py` through exactly one adapter exec, and returns typed OCC/result objects.
- `backend/src/sandbox/occ/setup.sh` — OCC setup submitted to the runtime/daemon by `occ/bootstrap.py` after bundle upload.
- `backend/src/sandbox/occ/engine.py` — `OCCEngine` Protocol; today's concrete engine becomes one impl.
- `backend/src/sandbox/occ/bootstrap.py` — registers `setup.sh`, bundle contributions, and OCC handlers at import time.
- `backend/src/sandbox/occ/handlers/` — thin server op adapters only:
  `write`, `edit`, `apply_changeset`, `commit`, `undo`.

Expected OCC shape after this step:

```
sandbox/occ/
    setup.sh
    client.py              # host-side typed OCC request client
    bootstrap.py           # registers setup.sh + server handlers
    handlers/              # server op adapters, no core OCC policy
        write.py
        edit.py
        apply_changeset.py
        commit.py
        undo.py
    changeset.py           # UpperChange classification + direct-merge decisions
    arbiter.py
    content_manager.py
    patcher.py
    time_machine.py
    write_coordinator/
    ledger_store.py
    hashing.py
    engine.py
    types.py
```

### Modify
- `sandbox/runtime/server.py` — import `sandbox.occ.bootstrap` / handlers so OCC ops register at import time. Server dispatch remains `OP_TABLE`-based; no per-OCC branch is added.
- `sandbox/runtime/pipelines.py`:
  - `edit_pipeline`: take a list of edits, drive OCC `apply` per edit, then a single `commit`. Atomic — partial apply rolls back on conflict via OCC arbiter.
  - `write_pipeline`: drive OCC `write` then `commit` in one in-sandbox process; one wire trip total.
- Rename OCC-internal verbs so they don't shadow the public ones:
  - `apply_edit` → `apply`
  - `undo_last_edit` → `undo`
  Public `edit` / `write` verbs land in Slice 6.

### Delete
- `backend/src/sandbox/code_intelligence/mutations/` (after move; this is the slice that retires the old location).

## Implementation tasks

1. `git mv` mutations → `sandbox/occ/`. Update imports across the codebase.
   Keep policy/coordination files at the OCC package root or existing
   subpackages (`changeset.py`, `arbiter.py`, `write_coordinator/`, etc.).
   Create `occ/handlers/` only for server request adapters.
2. Extract OCC types into `sandbox/occ/types.py`. If anything still imports from `core/types.py`, leave a re-export there until Slice 7.
3. Define `OCCEngine` Protocol with the minimal surface: `apply(...)`, `commit(...)`, `undo(...)`, `arbiter(...)`. Today's concrete engine implements it as-is.
4. Rename OCC verbs and audit every internal caller. Add a temporary lint check that grep-fails on `apply_edit` / `undo_last_edit` — remove the check at end of slice once zero hits.
5. Implement `OCCClient`. It owns all host-side OCC request routing and is the
   only place outside `runtime/` that constructs OCC server envelopes.
   It should expose typed methods for the operations that later back public
   `sandbox.api.write/edit`, plus internal operations such as
   `apply_changeset`, `commit`, and `undo` where needed by tests or migration
   shims. It does not import Overlay.
6. Add `occ/setup.sh` and make `occ/bootstrap.py` register it with
   `runtime/setup_orchestrator.py`. Keep setup idempotent; it may initialize
   ledger directories or OCC-local state, but it must not run shell/user
   commands.
7. Implement `edit_pipeline` and `write_pipeline` inside `runtime/pipelines.py`. They run in-process inside the sandbox, dispatched by `server.py`.
8. Register OCC handlers in `OP_TABLE` at module import time (via `sandbox/occ/handlers/__init__.py`). Handler modules call into OCC internals; they do not own policy themselves.

## Tests

- All existing OCC mutation tests pass at the new path.
- New `test_sandbox/test_occ/test_client.py`:
  - `OCCClient` performs exactly one adapter exec per request.
  - `OCCClient` serializes requests to `runtime/server.py` rather than
    reaching into handlers directly.
  - `OCCClient` does not import `sandbox.overlay`.
- New `test_sandbox/test_occ/test_bootstrap.py`:
  - `occ/bootstrap.py` registers `occ/setup.sh` with the setup orchestrator.
  - repeated setup registration/execution is idempotent.
- New `test_sandbox/test_occ/test_pipelines.py`:
  - `edit_pipeline` atomic across N edits → exactly one commit.
  - `write_pipeline` write+commit in one server call → one wire trip.
  - Conflict path: `edit_pipeline` returns `ConflictInfo(reason="patch_failed", path=...)` and the OCC ledger is unchanged.

## Exit criteria

- Build / ruff / tests green.
- `code_intelligence/mutations/` no longer exists.
- `sandbox/occ/client.py` is the only host-side route for OCC server
  requests; `sandbox/api.write/edit` are not wired yet in this slice.
- `sandbox/occ/setup.sh` is registered through `occ/bootstrap.py`.
- `sandbox/occ/handlers/` contains request adapters only; core OCC policy
  remains in `changeset.py`, `arbiter.py`, `write_coordinator/`, and sibling
  OCC internals.
- The two new pipelines are dispatch-reachable through `server.py`; `sandbox.api` does not yet expose them.
- `grep -r "apply_edit\|undo_last_edit" backend/src/` returns zero hits.

## Risks

- A renamed verb leaves a stale internal reference. Mitigation: temporary lint check + full grep audit.
- OCC arbiter rollback semantics differ between single-edit (legacy) and multi-edit (`edit_pipeline`) paths. Mitigation: the conflict test above is the gate.
- `OCCClient` becomes a second public API. Mitigation: importer allowlist
  permits it only from `sandbox.api.write`, `sandbox.api.edit`, runtime tests,
  and temporary migration shims; agent tools still import only `sandbox.api.*`.
