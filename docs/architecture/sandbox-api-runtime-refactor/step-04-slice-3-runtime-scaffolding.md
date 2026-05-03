# Step 4 — Slice 3 — Runtime layer scaffolding

**Goal.** Rename `code_intelligence/daemon/` → `sandbox/runtime/`. Introduce `bundle.py`, `setup_orchestrator.py`, and `server.py` as the single deployed in-sandbox guarded service with a peer-registered op table. `pipelines.py` exists but empty. The runtime also defines the peer setup contract: OCC, Overlay, and future plugins submit bundled `setup.sh` scripts through `setup_orchestrator.py`. The orchestrator still calls into the server via the legacy per-op snippet pathway.

**Boundary.** This step creates shared daemon/server infrastructure. It is not
a third domain module; the two peer modules are still `sandbox/occ/` and
`sandbox/overlay/`.

**Depends on.** Step 3 / Slice 2 (uses `raw_exec` to upload the bundle).

## Files

### Move (git mv to preserve history)
- `backend/src/sandbox/code_intelligence/daemon/` → `backend/src/sandbox/runtime/`.

### Add
- `backend/src/sandbox/runtime/bundle.py` — relocated from today's launcher; idempotent + content-addressed upload.
- `backend/src/sandbox/runtime/setup_orchestrator.py` — `SetupRegistry` with `register(setup_script)` and `run_all(sid)`. A setup script is a peer-owned bundled `setup.sh`; empty registry to start.
- `backend/src/sandbox/runtime/server.py` — replaces `command.py`'s switch. Generic guarded dispatcher: reads the JSON op envelope from stdin/argv, validates it, looks up `OP_TABLE: dict[str, Handler]`, invokes the handler, and writes the JSON result to stdout per §1.6. Peer modules populate `OP_TABLE` at import time.
- `backend/src/sandbox/runtime/pipelines.py` — empty stubs: `shell_pipeline`, `edit_pipeline`, `write_pipeline`. Filled in slices 4 and 5b.
- `backend/src/sandbox/code_intelligence/daemon/client.py` — compat shim that re-exports relocated symbols. Deleted in Slice 7.

### Delete
- `backend/src/sandbox/code_intelligence/daemon/command.py` (replaced by `runtime/server.py`).

## Implementation tasks

1. `git mv` the daemon directory.
2. Replace `command.py`'s switch with `server.py`. Initial `OP_TABLE` is empty — peers register in slices 4 and 5b. Until then, the orchestrator keeps using the per-op snippet pathway.
3. Keep `server.py` generic: envelope decode/validate, `OP_TABLE` lookup,
   handler invocation, result encoding, and structured errors only. Do not add
   one branch per OCC/Overlay op. A small explicit import of peer bootstraps is
   allowed later to load registrations, but request dispatch stays table-driven.
4. Resolve parent-doc §6 open question on host↔guest envelope: §1.6 result types serialized as JSON on stdout. No extra `{ok, result, error}` envelope. Document at the top of `server.py`.
5. Resolve parent-doc §6 open question on `SetupScript` shape: small frozen
   dataclass `SetupScript(name: str, package: str, relative_path: str)` pointing
   to a peer-owned bundled `setup.sh` file, for example
   `sandbox/occ/setup.sh` or `sandbox/overlay/setup.sh`.
   `setup_orchestrator.run_all(sid)` submits each script to the sandbox
   runtime/daemon in registration order after bundle upload. Bash content lives
   in the peer's `setup.sh`, not as an inline Python string.
6. Define the client/server convention for later slices: peer `client.py`
   modules are host-side typed route points that serialize a request to
   `runtime/server.py` and perform exactly one `adapter.exec` call. Do not
   add a generic public `runtime/client.py`.
7. Compat shim at `code_intelligence/daemon/client.py` re-exports `from sandbox.runtime.* import …` so legacy callers keep importing the legacy path.

## Tests

- Existing `test_sandbox/test_code_intelligence/test_daemon*` keep passing through the compat shim. Test directory move happens in Slice 8.
- New: `test_runtime/test_server_dispatch.py` — empty `OP_TABLE` returns `unknown_op` envelope; bad JSON in is a structured error; dispatch remains table-driven rather than a per-peer branch switch.
- New: `test_runtime/test_setup_orchestrator.py` — `run_all` is a no-op when registry empty; ordered execution preserved when populated; registered setup scripts are submitted as bundled `setup.sh` files rather than inline snippets.

## Exit criteria

- Build / ruff / tests green.
- The deployed in-sandbox script is `runtime/server.py`.
- `runtime/server.py` is a generic dispatcher; peer-specific behavior is
  registered through `OP_TABLE`.
- Peer setup registration supports concrete `setup.sh` files. OCC and Overlay
  consume it in later slices; this slice only establishes the contract.
- `from sandbox.code_intelligence.daemon.client import …` still resolves via the shim.
- `pipelines.py` exists, exports empty stubs, and is unreachable from agent paths.

## Risks

- Bundle deployment broken by the move. Mitigation: `bundle.py` is idempotent + content-addressed; `setup_orchestrator.run_all` runs *after* bundle upload; ordering is fixed.
- Compat shim drift if the `runtime/` API changes. Mitigation: shim is a re-export only; no logic.
