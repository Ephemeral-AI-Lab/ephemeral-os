# Slice 1 — Provider seam

**Goal.** Introduce `ProviderAdapter` Protocol and the Daytona adapter so callers can later route through `get_adapter(sid).exec`. No behavior change, no caller migration.

**Depends on.** None (entry slice).

## Files

### Add
- `backend/src/sandbox/providers/__init__.py`
- `backend/src/sandbox/providers/protocol.py` — `ProviderAdapter` Protocol with one method: `exec(...)`.
- `backend/src/sandbox/providers/daytona/__init__.py`
- `backend/src/sandbox/providers/daytona/adapter.py` — wraps today's `sandbox/daytona/transport.py` impl unchanged.
- `backend/src/sandbox/api/_registry.py` — `register_adapter(sid, adapter)`, `get_adapter(sid)`, `dispose_adapter(sid)`. Process-local dict + lock.

### Modify
- Lifecycle code that creates a sandbox: call `register_adapter` after the Daytona handle is built.
- Lifecycle code that disposes a sandbox: call `dispose_adapter`.
- `sandbox/api/transport.py` (`SandboxTransport` Protocol): becomes a structural alias of `ProviderAdapter` so existing type hints continue to resolve. Module docstring marks it deprecated.

### Move / Delete
- None.

## Implementation tasks

1. Define `ProviderAdapter` — exactly one method, mirroring today's `SandboxTransport.exec` signature byte-for-byte.
2. Implement `DaytonaProviderAdapter` by composing today's `DaytonaTransport`. No re-implementation of process.exec wire logic.
3. Add the registry as a process-local dict guarded by a single lock; `get_adapter` raises `KeyError` for unknown sandbox ids.
4. Wire lifecycle creation/disposal to `register_adapter` / `dispose_adapter`.
5. Make `SandboxTransport` a structural alias (or empty subclass) of `ProviderAdapter` so existing imports keep working.

## Tests

- New `test_sandbox/test_providers/test_registry.py` — register/get/dispose round-trip; KeyError on unknown id; idempotent dispose.
- New `test_sandbox/test_providers/test_daytona_adapter.py` — adapter delegates to underlying transport (mock or fixture); behavior parity with today's path.
- All existing sandbox tests pass unchanged.

## Exit criteria

- `make build`, `ruff check`, `make test` green.
- `grep -r "DaytonaTransport" backend/src/` shows new direct imports only in `providers/daytona/adapter.py` and lifecycle wiring; existing imports untouched.
- No agent or daemon caller has been migrated yet — that's by design.

## Risks

- Adapter loses fidelity vs today's transport. Mitigation: literal delegation; behavior parity is the test bar.
