# Step 6 — Slice 5b — Overlay peer relocation

**Goal.** Move overlay under `sandbox/overlay/`; add Overlay's `client.py` route point and `setup.sh`; register its setup and handlers with the runtime at import time. Introduce `shell_pipeline` as the only place overlay and OCC compose. With 5a having reshaped the seam (overlay = pure upperdir capture; OCC = merge-policy decider), 5b is mostly structural — relocate, rewire dispatch through `runtime/server.py`, enforce peer-isolation.

**Depends on.** Step 1 / Slice 5a and Step 5 / Slice 4 (both must be merged green first).

## Files

### Move
- `backend/src/sandbox/code_intelligence/overlay/` → `backend/src/sandbox/overlay/`.

### Add
- `backend/src/sandbox/overlay/__init__.py`.
- `backend/src/sandbox/overlay/client.py` — `OverlayClient`, the host-side typed route for every overlay/shell server request. It serializes the request, invokes `runtime/server.py` through exactly one adapter exec, and returns typed overlay/result objects.
- `backend/src/sandbox/overlay/setup.sh` — Overlay setup submitted to the runtime/daemon by `overlay/bootstrap.py` after bundle upload.
- `backend/src/sandbox/overlay/engine.py` — `OverlayEngine` Protocol returning `OverlayRunOutcome { upper_changes, overlay_rejected, conflict, ... }`.
- `backend/src/sandbox/overlay/bootstrap.py` — registers `setup.sh`, bundle contributions, and overlay handlers at import time.
- `backend/src/sandbox/overlay/handlers/run.py` — single in-sandbox handler: walk upperdir, capture raw `UpperChange` records, return `OverlayRunOutcome`.

### Modify
- `sandbox/runtime/pipelines.py::shell_pipeline`:
  - Call `overlay.run` first.
  - On overlay reject: short-circuit. No OCC call. Return `ShellResult` with `conflict` populated.
  - On overlay success: call `occ.apply_changeset` with `upper_changes`. Project the OCC verdict onto `ShellResult` — `gitinclude_changed_paths` / `gitignore_changed_paths` come from the OCC verdict, not from any pipeline-side classification (per §1.6).
- `sandbox/runtime/server.py`: import `sandbox.overlay.bootstrap` / handlers so overlay ops register in `OP_TABLE`; the `shell` op now dispatches to `shell_pipeline`. Server dispatch remains table-driven; no per-overlay branch is added.

### Delete
- `backend/src/sandbox/code_intelligence/overlay/` (after move).
- `backend/src/sandbox/overlay/process_exec.py` if it was carried over by the
  directory move. Its host-side request routing belongs in `overlay/client.py`;
  bundle upload/setup belongs in `runtime/bundle.py`, `runtime/setup_orchestrator.py`,
  and `overlay/setup.sh`.
- `backend/src/sandbox/overlay/daemon_local.py` if it was carried over by the
  directory move. Its in-sandbox execution/read-diff/cleanup responsibilities
  belong in `overlay/handlers/run.py` behind `runtime/server.py`.

## Implementation tasks

1. `git mv` overlay → `sandbox/overlay/`. Update imports.
2. Extract `OverlayEngine` Protocol; today's concrete engine implements it.
3. Implement `OverlayClient`. It owns all host-side overlay/shell request
   routing and is the only place outside `runtime/` that constructs overlay
   server envelopes. It does not import OCC.
4. Add `overlay/setup.sh` and make `overlay/bootstrap.py` register it with
   `runtime/setup_orchestrator.py`. Keep setup idempotent; mount/upperdir setup
   belongs here, while user command execution stays in the overlay handler.
5. Implement `shell_pipeline` per §1.5. The pipeline does not classify — it forwards `upper_changes` to `occ.apply_changeset` and projects the verdict onto `ShellResult`. Any `git check-ignore` or `direct_merge` import in `runtime/pipelines.py` is a structural review red flag.
6. Register overlay handlers in `server.OP_TABLE` at module import time.
7. Add lint allowlist tests:
   - `from sandbox.occ` is forbidden inside `sandbox/overlay/`.
   - `from sandbox.overlay` is forbidden inside `sandbox/occ/`.
8. Split the Step 1 temporary execution shims:
   - `process_exec.py` host-side request/envelope logic → `overlay/client.py`;
     setup/upload logic → `runtime/bundle.py`, `runtime/setup_orchestrator.py`,
     and `overlay/setup.sh`.
   - `daemon_local.py` in-sandbox run/read-diff/cleanup logic →
     `overlay/handlers/run.py`.
   - Delete the shim files after those responsibilities are covered.
9. Confirm 5a's reshaped overlay package transplants cleanly to `sandbox/overlay/` with no remaining gitignore / check-ignore surfaces.

## Tests

- All overlay tests pass at the new path.
- New `test_sandbox/test_overlay/test_client.py`:
  - `OverlayClient` performs exactly one adapter exec per request.
  - `OverlayClient` serializes requests to `runtime/server.py` rather than
    reaching into handlers directly.
  - `OverlayClient` does not import `sandbox.occ`.
- New `test_sandbox/test_overlay/test_bootstrap.py`:
  - `overlay/bootstrap.py` registers `overlay/setup.sh` with the setup orchestrator.
  - repeated setup registration/execution is idempotent.
- New `test_sandbox/test_runtime/test_shell_pipeline.py`:
  - **One wire trip per shell op** — assert exactly one `adapter.exec` invocation per pipeline call.
  - Overlay reject → no `occ.apply_changeset` invocation; `ShellResult.conflict` populated.
  - Overlay success → ledger advances on gitinclude only; live workspace updated for gitignore/external; `ShellResult.gitinclude_changed_paths` / `gitignore_changed_paths` partitioned per OCC's verdict.
- Lint allowlist test: peer-isolation invariants enforced.

## Exit criteria

- Build / ruff / tests green.
- `code_intelligence/overlay/` no longer exists.
- `sandbox/overlay/client.py` is the only host-side route for overlay/shell
  server requests.
- `sandbox/overlay/setup.sh` is registered through `overlay/bootstrap.py`.
- `process_exec.py` and `daemon_local.py` do not exist under
  `sandbox/overlay/`; their responsibilities are represented by
  `overlay/client.py`, `overlay/handlers/run.py`, and the runtime setup files.
- Peer-isolation lint test passes (overlay ↔ OCC mutual non-import).
- One-wire-trip assertion holds for every `shell_pipeline` test.

## Risks

- A refactor mistake reintroduces a second wire trip. Mitigation: explicit one-wire-trip-per-op assertion in pipeline tests.
- `shell_pipeline` accidentally re-introduces classification at the seam (e.g., a helper that re-runs `git check-ignore` to "enrich" the OCC verdict). Mitigation: peer-isolation lint forbids gitignore-tooling imports inside `sandbox/overlay/` and inside `runtime/pipelines.py`; reviewers reject any classification helper added in this slice.
- 5a's lifted helpers (`direct_merge_factory`, `narrow_prune_opaque_factory`) live under `mutations/` post-5a; 5b's OCC relocation (Slice 4, already merged) means they're now at `sandbox/occ/`. Mitigation: confirm via grep at the start of 5b that the helpers landed in OCC's tree and are not still imported from the old `code_intelligence/mutations/` path before relocating overlay.
- `OverlayClient` becomes a second public API. Mitigation: importer allowlist
  permits it only from `sandbox.api.shell`, runtime tests, and temporary
  migration shims; agent tools still import only `sandbox.api.*`.
