# Step 3 — Slice 2 — Public `sandbox.api.raw_exec`

**Goal.** Introduce the un-guarded primitive used only by runtime/setup/lifecycle/debug paths. Migrate today's host-side bundle upload + selected lifecycle/debug raw exec calls to import it. Agent tools, audited API, CI internals, OCC content management, and `code_intelligence/daemon/client.py` are explicitly *not* migrated yet.

**Depends on.** Step 2 / Slice 1.

## Files

### Add
- `backend/src/sandbox/api/raw_exec.py` — `def raw_exec(sid, cmd, ...) -> RawExecResult: return get_adapter(sid).exec(cmd, ...)`, where `get_adapter` comes from `sandbox.providers.registry`.
- `backend/src/sandbox/runtime/__init__.py`
- `backend/src/sandbox/runtime/bundle.py` — host-side bundle composition + idempotent upload moved out of `code_intelligence/daemon/launcher.py`; uses `sandbox.api.raw_exec` by sandbox id.
- `test_sandbox/test_api/test_importer_allowlist.py` — AST-walk test (per parent doc §6: unit test, not custom ruff rule).

### Keep
- `backend/src/sandbox/api/models.py` — keep the existing request/result models. `RawExecResult` already exists in this checkout and must remain field-compatible; the full §1.6 hierarchy lands in Slice 6.
- `backend/src/sandbox/api/transport.py` — legacy wide `SandboxTransport` remains for byte I/O and checked writes.
- `backend/src/sandbox/daytona/transport.py` — legacy wide Daytona transport remains because `DaytonaProviderAdapter` composes it.
- `backend/src/sandbox/code_intelligence/daemon/client.py` — stays on legacy transport in this slice.

### Modify
- Today's host-side runtime bundle upload helpers (currently in `sandbox/code_intelligence/daemon/launcher.py`): move/split into `sandbox/runtime/bundle.py` and call `sandbox.api.raw_exec` instead of `SandboxTransport.exec`.
- Lifecycle paths that only need raw command execution by sandbox id: migrate to `raw_exec` after provider adapter registration is guaranteed.
- Debug paths that today call provider SDK `process.exec` directly, such as the sandbox debug exec route: migrate to `raw_exec`.
- `code_intelligence/daemon/launcher.py`: shrink to the legacy `DaemonLauncher` compatibility wrapper used by `daemon/client.py`; it should delegate bundle upload to `runtime/bundle.py` where possible without migrating `daemon/client.py`.

## Implementation tasks

1. Reuse the existing `RawExecResult` in `sandbox/api/models.py`; only adjust it if field compatibility with provider `exec` requires it. Do not strip existing models in this slice.
2. Implement `raw_exec` as a thin async wrapper. No retry, no logging — those layer on later if needed.
3. Move host-side bundle composition/upload into `runtime/bundle.py`. Keep the idempotent marker/hash behavior from `daemon/launcher.py`, but route command execution through `raw_exec(sid, ...)`.
4. Migrate only the allowlisted raw-exec caller categories above. Agent tools, audited API, CI internals, OCC content management, and `daemon/client.py` continue using the old transport — both code paths coexist.
5. For synchronous lifecycle paths, either keep the existing direct SDK call until an async-safe bridge exists or use the repo's established sync bridge. Do not introduce ad hoc event-loop blocking.
6. Importer allowlist test: fail if `sandbox.api.raw_exec` is imported by anything outside this set:
   - `sandbox/runtime/bundle.py`.
   - `sandbox/lifecycle/*`.
   - debug paths (enumerate explicitly).
   New entries to the set require an allowlist edit, not a silent expansion.

## Tests

- `test_sandbox/test_api/test_raw_exec.py` — delegates to `sandbox.providers.registry.get_adapter(...).exec`; propagates `RawExecResult`; unknown sandbox id surfaces cleanly.
- `test_sandbox/test_api/test_raw_exec_import_allowlist.py` — covers `raw_exec` importer restrictions.
- `test_sandbox/test_runtime/test_bundle.py` — idempotent marker/hash behavior; upload uses `raw_exec` rather than `SandboxTransport`.
- Existing tests pass unchanged.

## Exit criteria

- Build / ruff / tests green.
- `grep -r "from sandbox.api.raw_exec" backend/src/` matches only allowlisted modules.
- `sandbox/runtime/bundle.py` owns host-side bundle upload; `daemon/launcher.py`
  is only a legacy launcher wrapper for `daemon/client.py`.
- `sandbox/api/models.py` still contains existing API models; no request/result
  surface is deleted in this slice.
- `runtime/server.py`, `runtime/pipelines.py`, and
  `runtime/setup_orchestrator.py` do not exist yet.
- Agent tool imports unchanged in this slice.
- `SandboxTransport` and `DaytonaTransport` remain in place.

## Risks

- A migrated call site silently breaks because the old transport had a richer return shape. Mitigation: keep `RawExecResult` field-compatible with what callers actually used.
- Lifecycle ordering bug: `raw_exec` is called before provider adapter
  registration. Mitigation: registration must happen immediately after sandbox
  id/provider handle creation; tests cover unknown-id behavior.
- Scope creep into runtime server work. Mitigation: this slice may add
  `runtime/bundle.py` only; server, pipelines, and setup orchestration are
  Slice 3.
