# Step 4 — Slice 3 — Runtime layer scaffolding

**Goal.** Finish the runtime scaffolding started in Step 3. Replace the old `code_intelligence/daemon/command.py` dispatch switch with `sandbox/runtime/server.py`. Introduce `setup_orchestrator.py` and `server.py` as the single deployed in-sandbox guarded service with a peer-registered op table. `pipelines.py` exists but empty. The runtime also defines the peer setup contract: OCC, Overlay, and future plugins submit bundled `setup.sh` scripts through `setup_orchestrator.py`. Legacy daemon clients keep working through a temporary compatibility route, not through the new server contract.

**Boundary.** This step creates shared daemon/server infrastructure. It is not
a third domain module; the two peer modules are still `sandbox/occ/` and
`sandbox/overlay/`. Do not move OCC/edit-ledger state into `runtime/`.
`code_intelligence/daemon/{storage,ledger_store,paths}.py` stay in the legacy
tree until the OCC relocation slice decides their final home.

**Depends on.** Step 3 / Slice 2 (`runtime/bundle.py` exists and uses `raw_exec` to upload the bundle).

## Files

### Move / Split
- `backend/src/sandbox/code_intelligence/daemon/command.py` dispatch logic → `backend/src/sandbox/runtime/server.py`.
- Old `{ok, result, error}` compatibility behavior needed by `DaemonCommandClient` → `backend/src/sandbox/runtime/legacy_command_client.py` if existing daemon tests need the old envelope during this slice.
- `backend/src/sandbox/runtime/bundle.py` already exists from Step 3. Update its bundle contents and stale comments so it includes `sandbox/runtime/{server,pipelines,setup_orchestrator}.py` and no longer documents `sandbox.code_intelligence.daemon.command` as the target entry.
- `backend/src/sandbox/code_intelligence/daemon/client.py` becomes a shim/re-export to the temporary runtime legacy command client.

### Add
- `backend/src/sandbox/runtime/setup_orchestrator.py` — `SetupRegistry` with `register(setup_script)` and `run_all(sid)`. A setup script is a peer-owned bundled `setup.sh`; empty registry to start.
- `backend/src/sandbox/runtime/server.py` — replaces `command.py`'s switch. Generic guarded dispatcher: reads the JSON op envelope from stdin/argv, validates it, looks up `OP_TABLE: dict[str, Handler]`, invokes the handler, and writes the JSON result to stdout per §1.6. Peer modules populate `OP_TABLE` at import time.
- `backend/src/sandbox/runtime/pipelines.py` — empty stubs: `shell_pipeline`, `edit_pipeline`, `write_pipeline`. Filled in slices 4 and 5b.
- `backend/src/sandbox/runtime/legacy_command_client.py` — temporary legacy envelope adapter for existing daemon callers only, if needed. It is not public runtime API and is deleted in Slice 7.

### Keep Legacy In Place
- `backend/src/sandbox/code_intelligence/daemon/storage.py` — legacy daemon/OCC state; do not move to runtime.
- `backend/src/sandbox/code_intelligence/daemon/ledger_store.py` — OCC/edit-ledger state; move only in the OCC slice.
- `backend/src/sandbox/code_intelligence/daemon/paths.py` — path helpers tied to legacy daemon/OCC state; do not move to runtime.
- `backend/src/sandbox/code_intelligence/daemon/wire.py` — keep only if the temporary legacy client still needs old request/result conversions; otherwise delete with the compatibility adapter.

### Delete
- `backend/src/sandbox/code_intelligence/daemon/command.py` (replaced by `runtime/server.py`).
- `backend/src/sandbox/code_intelligence/daemon/launcher.py` once import audit shows all upload/bootstrap users go through `runtime/bundle.py` or the temporary runtime legacy client.

## Implementation tasks

1. Move/split only daemon dispatch/bootstrap code into `sandbox/runtime/`.
   Preserve `runtime/bundle.py` from Step 3 and update its bundle manifest to include the new runtime files.
2. Replace `command.py`'s switch with `server.py`. Initial `OP_TABLE` is empty — peers register in slices 4 and 5b. Until then, compatibility callers use the temporary legacy adapter rather than adding peer-specific branches to `server.py`.
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
7. If old daemon tests still require `{ok, result, error}`, implement that in
   `runtime/legacy_command_client.py`. Do not make `server.py` emit the old
   envelope.
8. Compat shim at `code_intelligence/daemon/client.py` re-exports the temporary
   runtime legacy client so legacy callers keep importing the legacy path.
9. Leave `daemon/{storage,ledger_store,paths}.py` in place for Slice 4/OCC.

## Tests

- Existing `test_sandbox/test_code_intelligence/test_daemon*` keep passing through the compat shim where still relevant. Behavior assertions should move toward `runtime/server.py` dispatch semantics.
- Bundle tests expect the uploaded bundle to include `sandbox/runtime/server.py`, `sandbox/runtime/pipelines.py`, and `sandbox/runtime/setup_orchestrator.py`, not `sandbox/code_intelligence/daemon/command.py`.
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
- `code_intelligence/daemon/command.py` no longer exists.
- `code_intelligence/daemon/{storage,ledger_store,paths}.py` have not moved to
  `sandbox/runtime/`.
- `runtime/bundle.py` bundles runtime files and has no stale dependency on
  `sandbox.code_intelligence.daemon.command`.
- `runtime/bundle.py` remains host-side and is not included in the extracted
  tarball; bundled runtime modules import cleanly without host-only
  `sandbox.api.raw_exec`.
- `pipelines.py` exists, exports empty stubs, and is unreachable from agent paths.

## Risks

- Bundle deployment broken by the move. Mitigation: `bundle.py` was isolated in Step 3 and remains idempotent + content-addressed; `setup_orchestrator.run_all` runs *after* bundle upload; ordering is fixed.
- Compat shim drift if the `runtime/` API changes. Mitigation: keep legacy
  compatibility isolated in `runtime/legacy_command_client.py`, not spread
  through `server.py`.
- OCC state accidentally lands in runtime. Mitigation: explicit exit check that
  `storage.py`, `ledger_store.py`, and `paths.py` remain outside
  `sandbox/runtime/` until the OCC slice.
