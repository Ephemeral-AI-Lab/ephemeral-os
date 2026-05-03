# Slice 5b — Overlay peer relocation

**Goal.** Move overlay under `sandbox/overlay/`. Introduce `shell_pipeline` as the only place overlay and OCC compose. Delete the OCC-coupling code path that 5a stripped logic out of.

**Depends on.** Slice 5a (must be merged green first).

## Files

### Move
- `backend/src/sandbox/code_intelligence/overlay/` → `backend/src/sandbox/overlay/`.

### Add
- `backend/src/sandbox/overlay/__init__.py`
- `backend/src/sandbox/overlay/engine.py` — `OverlayEngine` Protocol.
- `backend/src/sandbox/overlay/bootstrap.py` — overlay's setup-script registration (mount upper layer, etc.).
- `backend/src/sandbox/overlay/handlers/run.py` — single in-sandbox handler: mount overlay, run shell, capture dirty paths, return `OverlayRunOutcome`.

### Modify
- `sandbox/runtime/pipelines.py::shell_pipeline`:
  - Call `overlay.run` first.
  - On overlay reject: short-circuit. No OCC. Return `ShellResult` with `conflict` populated.
  - On overlay success: call `occ.commit` with `dirty_changes`. Return `ShellResult` with `gitinclude_changed_paths` / `gitignore_changed_paths` partitioned per §1.6.
- `sandbox/runtime/entrypoint.py`: register overlay handlers in `OP_TABLE`; the `shell` op now dispatches to `shell_pipeline`.

### Delete
- `backend/src/sandbox/code_intelligence/overlay/` (after move).
- The OCC-driving code path that 5a left as dead code in `OverlayCommandCommitter`. After this slice, the `dirty_changes`-returning shape is the only shape.

## Implementation tasks

1. `git mv` overlay → `sandbox/overlay/`. Update imports.
2. Extract `OverlayEngine` Protocol; today's concrete engine implements it.
3. Implement `shell_pipeline` per §1.5. Compute the gitinclude/gitignore split: a path lands in `gitignore_changed_paths` iff it falls under a `.gitignore` rule (overlay-merged but not ledgered); everything else goes to `gitinclude_changed_paths`.
4. Register overlay handlers in `entrypoint.OP_TABLE` at module import time.
5. Add lint allowlist tests:
   - `from sandbox.occ` is forbidden inside `sandbox/overlay/`.
   - `from sandbox.overlay` is forbidden inside `sandbox/occ/`.
6. Delete the in-place stripped code from 5a. Only `shell_pipeline` composes the two peers from now on.

## Tests

- All overlay tests pass at the new path.
- New `test_sandbox/test_runtime/test_shell_pipeline.py`:
  - **One wire trip per shell op** — assert exactly one `adapter.exec` invocation per pipeline call.
  - Overlay reject → no ledger update; `ShellResult.conflict` populated.
  - Overlay success → ledger updated; `gitinclude_changed_paths` and `gitignore_changed_paths` partitioned correctly per `.gitignore`.
- Lint allowlist test: peer-isolation invariants enforced.

## Exit criteria

- Build / ruff / tests green.
- `code_intelligence/overlay/` no longer exists.
- Peer-isolation lint test passes (overlay ↔ OCC mutual non-import).
- One-wire-trip assertion holds for every `shell_pipeline` test.

## Risks

- A refactor mistake introduces a second wire trip. Mitigation: explicit one-wire-trip-per-op assertion in pipeline tests.
- `.gitignore` evaluation differs between caller and pipeline. Mitigation: the partitioning test owns the split; one canonical evaluator inside `shell_pipeline`.
