# Step 5 — Slice 4 — OCC peer relocation

**Goal.** Move OCC under `sandbox/occ/`; register its handlers with the entrypoint at import time. Wire `edit_pipeline` (multi-edit OCC apply + atomic commit) and `write_pipeline` (OCC write + commit) end-to-end inside the sandbox. Both are reachable through the entrypoint but **not yet exposed via `sandbox.api`** — that's Slice 6.

**Depends on.** Step 4 / Slice 3.

## Files

### Move
- `backend/src/sandbox/code_intelligence/mutations/` → `backend/src/sandbox/occ/handlers/`.
- OCC-relevant pieces of `backend/src/sandbox/code_intelligence/core/types.py` → `backend/src/sandbox/occ/types.py`. Non-OCC pieces stay (or migrate under `plugins-refactor.md`).

### Add
- `backend/src/sandbox/occ/__init__.py`
- `backend/src/sandbox/occ/engine.py` — `OCCEngine` Protocol; today's concrete engine becomes one impl.
- `backend/src/sandbox/occ/bootstrap.py` — empty for now; OCC's bootstrap is just entrypoint deployment.

### Modify
- `sandbox/runtime/entrypoint.py` — import `sandbox.occ.handlers` so handlers register at import time.
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

1. `git mv` mutations → `sandbox/occ/handlers/`. Update imports across the codebase.
2. Extract OCC types into `sandbox/occ/types.py`. If anything still imports from `core/types.py`, leave a re-export there until Slice 7.
3. Define `OCCEngine` Protocol with the minimal surface: `apply(...)`, `commit(...)`, `undo(...)`, `arbiter(...)`. Today's concrete engine implements it as-is.
4. Rename OCC verbs and audit every internal caller. Add a temporary lint check that grep-fails on `apply_edit` / `undo_last_edit` — remove the check at end of slice once zero hits.
5. Implement `edit_pipeline` and `write_pipeline` inside `runtime/pipelines.py`. They run in-process inside the sandbox, dispatched by `entrypoint.py`.
6. Register OCC handlers in `OP_TABLE` at module import time (via `sandbox/occ/handlers/__init__.py`).

## Tests

- All existing OCC mutation tests pass at the new path.
- New `test_sandbox/test_occ/test_pipelines.py`:
  - `edit_pipeline` atomic across N edits → exactly one commit.
  - `write_pipeline` write+commit in one entrypoint call → one wire trip.
  - Conflict path: `edit_pipeline` returns `ConflictInfo(reason="patch_failed", path=...)` and the OCC ledger is unchanged.

## Exit criteria

- Build / ruff / tests green.
- `code_intelligence/mutations/` no longer exists.
- The two new pipelines are dispatch-reachable through `entrypoint.py`; `sandbox.api` does not yet expose them.
- `grep -r "apply_edit\|undo_last_edit" backend/src/` returns zero hits.

## Risks

- A renamed verb leaves a stale internal reference. Mitigation: temporary lint check + full grep audit.
- OCC arbiter rollback semantics differ between single-edit (legacy) and multi-edit (`edit_pipeline`) paths. Mitigation: the conflict test above is the gate.
