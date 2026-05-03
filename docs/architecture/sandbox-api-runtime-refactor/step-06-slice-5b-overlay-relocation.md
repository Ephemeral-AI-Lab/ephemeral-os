# Step 6 — Slice 5b — Overlay peer relocation

**Goal.** Move overlay under `sandbox/overlay/`. Introduce `shell_pipeline` as the only place overlay and OCC compose. With 5a having reshaped the seam (overlay = pure upperdir capture; OCC = merge-policy decider), 5b is purely structural — relocate, rewire dispatch, enforce peer-isolation.

**Depends on.** Step 1 / Slice 5a and Step 5 / Slice 4 (both must be merged green first).

## Files

### Move
- `backend/src/sandbox/code_intelligence/overlay/` → `backend/src/sandbox/overlay/`.

### Add
- `backend/src/sandbox/overlay/__init__.py`.
- `backend/src/sandbox/overlay/engine.py` — `OverlayEngine` Protocol returning `OverlayRunOutcome { upper_changes, overlay_rejected, conflict, ... }`.
- `backend/src/sandbox/overlay/bootstrap.py` — overlay's setup-script registration (mount upper layer, etc.).
- `backend/src/sandbox/overlay/handlers/run.py` — single in-sandbox handler: walk upperdir, capture raw `UpperChange` records, return `OverlayRunOutcome`.

### Modify
- `sandbox/runtime/pipelines.py::shell_pipeline`:
  - Call `overlay.run` first.
  - On overlay reject: short-circuit. No OCC call. Return `ShellResult` with `conflict` populated.
  - On overlay success: call `occ.apply_changeset` with `upper_changes`. Project the OCC verdict onto `ShellResult` — `gitinclude_changed_paths` / `gitignore_changed_paths` come from the OCC verdict, not from any pipeline-side classification (per §1.6).
- `sandbox/runtime/entrypoint.py`: register overlay handlers in `OP_TABLE`; the `shell` op now dispatches to `shell_pipeline`.

### Delete
- `backend/src/sandbox/code_intelligence/overlay/` (after move).

## Implementation tasks

1. `git mv` overlay → `sandbox/overlay/`. Update imports.
2. Extract `OverlayEngine` Protocol; today's concrete engine implements it.
3. Implement `shell_pipeline` per §1.5. The pipeline does not classify — it forwards `upper_changes` to `occ.apply_changeset` and projects the verdict onto `ShellResult`. Any `git check-ignore` or `direct_merge` import in `runtime/pipelines.py` is a structural review red flag.
4. Register overlay handlers in `entrypoint.OP_TABLE` at module import time.
5. Add lint allowlist tests:
   - `from sandbox.occ` is forbidden inside `sandbox/overlay/`.
   - `from sandbox.overlay` is forbidden inside `sandbox/occ/`.
6. Confirm 5a's reshaped overlay package transplants cleanly to `sandbox/overlay/` with no remaining gitignore / check-ignore surfaces.

## Tests

- All overlay tests pass at the new path.
- New `test_sandbox/test_runtime/test_shell_pipeline.py`:
  - **One wire trip per shell op** — assert exactly one `adapter.exec` invocation per pipeline call.
  - Overlay reject → no `occ.apply_changeset` invocation; `ShellResult.conflict` populated.
  - Overlay success → ledger advances on gitinclude only; live workspace updated for gitignore/external; `ShellResult.gitinclude_changed_paths` / `gitignore_changed_paths` partitioned per OCC's verdict.
- Lint allowlist test: peer-isolation invariants enforced.

## Exit criteria

- Build / ruff / tests green.
- `code_intelligence/overlay/` no longer exists.
- Peer-isolation lint test passes (overlay ↔ OCC mutual non-import).
- One-wire-trip assertion holds for every `shell_pipeline` test.

## Risks

- A refactor mistake reintroduces a second wire trip. Mitigation: explicit one-wire-trip-per-op assertion in pipeline tests.
- `shell_pipeline` accidentally re-introduces classification at the seam (e.g., a helper that re-runs `git check-ignore` to "enrich" the OCC verdict). Mitigation: peer-isolation lint forbids gitignore-tooling imports inside `sandbox/overlay/` and inside `runtime/pipelines.py`; reviewers reject any classification helper added in this slice.
- 5a's lifted helpers (`direct_merge_factory`, `narrow_prune_opaque_factory`) live under `mutations/` post-5a; 5b's OCC relocation (Slice 4, already merged) means they're now at `sandbox/occ/`. Mitigation: confirm via grep at the start of 5b that the helpers landed in OCC's tree and are not still imported from the old `code_intelligence/mutations/` path before relocating overlay.
