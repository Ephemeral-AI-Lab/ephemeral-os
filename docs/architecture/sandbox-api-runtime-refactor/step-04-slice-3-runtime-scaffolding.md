# Step 4 — Slice 3 — Runtime layer scaffolding

**Goal.** Rename `code_intelligence/daemon/` → `sandbox/runtime/`. Introduce `bundle.py`, `setup_orchestrator.py`, and `entrypoint.py` as the single deployed in-sandbox script with a peer-registered op table. `pipelines.py` exists but empty. The orchestrator still calls into the entrypoint via the legacy per-op snippet pathway.

**Depends on.** Step 3 / Slice 2 (uses `raw_exec` to upload the bundle).

## Files

### Move (git mv to preserve history)
- `backend/src/sandbox/code_intelligence/daemon/` → `backend/src/sandbox/runtime/`.

### Add
- `backend/src/sandbox/runtime/bundle.py` — relocated from today's launcher; idempotent + content-addressed upload.
- `backend/src/sandbox/runtime/setup_orchestrator.py` — `SetupRegistry` with `register(setup_script)` and `run_all(sid)`. Empty registry to start.
- `backend/src/sandbox/runtime/entrypoint.py` — replaces `command.py`'s switch. Holds `OP_TABLE: dict[str, Handler]` populated at peer-import time. Reads JSON op envelope from stdin/argv, writes JSON result to stdout per §1.6.
- `backend/src/sandbox/runtime/pipelines.py` — empty stubs: `shell_pipeline`, `edit_pipeline`, `write_pipeline`. Filled in slices 4 and 5b.
- `backend/src/sandbox/code_intelligence/daemon/client.py` — compat shim that re-exports relocated symbols. Deleted in Slice 7.

### Delete
- `backend/src/sandbox/code_intelligence/daemon/command.py` (replaced by `runtime/entrypoint.py`).

## Implementation tasks

1. `git mv` the daemon directory.
2. Replace `command.py`'s switch with `entrypoint.py`. Initial `OP_TABLE` is empty — peers register in slices 4 and 5b. Until then, the orchestrator keeps using the per-op snippet pathway.
3. Resolve parent-doc §6 open question on host↔guest envelope: §1.6 result types serialized as JSON on stdout. No extra `{ok, result, error}` envelope. Document at the top of `entrypoint.py`.
4. Resolve parent-doc §6 open question on `SetupScript` shape: small frozen dataclass `SetupScript(name: str, run: Callable[[str], None])` invoked with the sandbox id. Bash blob vs script file is a per-peer choice inside `run`.
5. Compat shim at `code_intelligence/daemon/client.py` re-exports `from sandbox.runtime.* import …` so legacy callers keep importing the legacy path.

## Tests

- Existing `test_sandbox/test_code_intelligence/test_daemon*` keep passing through the compat shim. Test directory move happens in Slice 8.
- New: `test_runtime/test_entrypoint_dispatch.py` — empty `OP_TABLE` returns `unknown_op` envelope; bad JSON in is a structured error.
- New: `test_runtime/test_setup_orchestrator.py` — `run_all` is a no-op when registry empty; ordered execution preserved when populated.

## Exit criteria

- Build / ruff / tests green.
- The deployed in-sandbox script is `runtime/entrypoint.py`.
- `from sandbox.code_intelligence.daemon.client import …` still resolves via the shim.
- `pipelines.py` exists, exports empty stubs, and is unreachable from agent paths.

## Risks

- Bundle deployment broken by the move. Mitigation: `bundle.py` is idempotent + content-addressed; `setup_orchestrator.run_all` runs *after* bundle upload; ordering is fixed.
- Compat shim drift if the `runtime/` API changes. Mitigation: shim is a re-export only; no logic.
