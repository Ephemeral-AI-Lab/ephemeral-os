# Step 2 — Slice 1 — Provider seam

**Goal.** Introduce `ProviderAdapter` Protocol and the Daytona adapter so callers can later route through `get_adapter(sid).exec`. No behavior change, no caller migration, and no deletion or narrowing of the legacy wide transport.

**Depends on.** Step 1 merged for implementation order. Architecturally independent.

## Files

### Add
- `backend/src/sandbox/providers/__init__.py`
- `backend/src/sandbox/providers/protocol.py` — `ProviderAdapter` Protocol with one method: `exec(...)`.
- `backend/src/sandbox/providers/registry.py` — `register_adapter(sid, adapter)`, `get_adapter(sid)`, `dispose_adapter(sid)`. Process-local dict + lock.
- `backend/src/sandbox/providers/daytona/__init__.py`
- `backend/src/sandbox/providers/daytona/adapter.py` — `DaytonaProviderAdapter`; delegates `exec(...)` to today's `sandbox/daytona/transport.py` implementation unchanged.

### Modify
- Lifecycle code that creates/starts/recovers a sandbox: call `register_adapter` after the Daytona handle is built.
- Lifecycle code that deletes a sandbox: call `dispose_adapter`.
- Context transport construction for pre-existing sandboxes: register the adapter if it is missing.
- `sandbox/api/transport.py` (`SandboxTransport` Protocol): keep the legacy
  wide transport shape and mark it deprecated. It may extend `ProviderAdapter`,
  but it must still declare `read_bytes`, `read_bytes_batch`, `write_bytes`,
  and `apply_diff_batch_checked` until every production caller has migrated.
- `sandbox/daytona/transport.py`: keep the legacy wide `DaytonaTransport`
  implementation. `DaytonaProviderAdapter` composes it; do not move or delete
  wide methods in this slice.

### Move / Delete
- None.

## Implementation tasks

1. Define `ProviderAdapter` — exactly one method, mirroring today's `SandboxTransport.exec` signature byte-for-byte.
2. Implement `DaytonaProviderAdapter` by composing today's `DaytonaTransport`. No re-implementation of process.exec wire logic and no duplicate Daytona SDK coupling.
3. Add the provider registry as a process-local dict guarded by a single lock under `sandbox/providers/registry.py`; `get_adapter` raises `KeyError` for unknown sandbox ids.
4. Wire lifecycle creation/start/recovery/delete to `register_adapter` / `dispose_adapter`.
5. Register missing adapters from the context transport builder so pre-existing sandboxes can later use `get_adapter(sid).exec`.
6. Keep `SandboxTransport` as a deprecated legacy superset of `ProviderAdapter`:

   ```python
   class SandboxTransport(ProviderAdapter, Protocol):
       async def read_bytes(...): ...
       async def read_bytes_batch(...): ...
       async def write_bytes(...): ...
       async def apply_diff_batch_checked(...): ...
   ```

   This keeps existing imports and tests working while making `exec` the new
   provider source of truth for later slices.

## Tests

- New `test_sandbox/test_providers/test_registry.py` — register/get/dispose round-trip; KeyError on unknown id; idempotent dispose.
- New `test_sandbox/test_providers/test_daytona_adapter.py` — adapter delegates to underlying transport (mock or fixture); context transport construction registers a provider adapter for pre-existing sandboxes.
- All existing sandbox tests pass unchanged.

## Exit criteria

- `make build`, `ruff check`, `make test` green.
- `grep -r "DaytonaTransport" backend/src/sandbox` shows direct construction only in `providers/daytona/adapter.py` and the temporary legacy transport builder; Slice 7 removes the latter.
- `sandbox/api/transport.py` still exposes the full legacy `SandboxTransport`
  protocol. `read_bytes`, `read_bytes_batch`, `write_bytes`, and
  `apply_diff_batch_checked` remain available for existing CI/API callers.
- `sandbox/providers/registry.py` is the only adapter registry; no
  `sandbox/api/_registry.py` is introduced in this slice.
- No agent or daemon caller has been migrated yet — that's by design.

## Risks

- Adapter loses fidelity vs today's transport. Mitigation: literal delegation; behavior parity is the test bar.
- Narrowing `SandboxTransport` too early breaks production callers that still
  need byte I/O and checked batch writes. Mitigation: keep it as a deprecated
  superset until the delete-legacy slice.
