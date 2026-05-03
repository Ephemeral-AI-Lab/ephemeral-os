# Step 8 — Slice 7 — Delete the old client + transport

**Goal.** Remove every superseded surface. After this slice, `code_intelligence/` is gone and `ProviderAdapter` is the only sandbox path.

**Depends on.** Step 7 / Slice 6 (verbs and agent tools fully migrated).

## Files

### Delete
- `backend/src/sandbox/code_intelligence/daemon/client.py` — compat shim from Slice 3.
- `backend/src/sandbox/code_intelligence/` — the umbrella; expected empty by this point. Verify with `find ... -type f` before delete.
- `backend/src/sandbox/api/sandbox_api.py`
- `backend/src/sandbox/api/audited_sandbox_api.py`
- `backend/src/sandbox/api/attribution.py`
- `backend/src/sandbox/api/audit.py`
- `backend/src/sandbox/api/bash.py`
- `backend/src/sandbox/api/file_commands.py`
- `backend/src/sandbox/api/transport.py` — `SandboxTransport` Protocol; every reference now uses `ProviderAdapter`.
- `backend/src/sandbox/api/code_intelligence_api.py` and `code_intelligence_impl.py`, if present.
- `backend/src/sandbox/daytona/transport.py` — `ProviderAdapter` is the only path.

### Modify
- `backend/src/sandbox/api/models.py`: strip query-side types that were left as references during migration but no caller imports anymore. The query surface is migrating out under `plugins-refactor.md`; only delete here what shows zero hits.
- `backend/src/sandbox/__init__.py`: trim re-export surface accordingly.

## Implementation tasks

1. Pre-delete grep audit. Each must return zero production hits before its file is deleted:
   - `grep -r "from sandbox.code_intelligence" backend/src/`
   - `grep -r "SandboxTransport" backend/src/`
   - `grep -r "audited_sandbox_api\|attribution\|sandbox.api.audit\|sandbox.api.bash\|file_commands" backend/src/`
   - `grep -r "from sandbox.daytona.transport" backend/src/`
   If a hit remains, it's the *importer* that's the bug — fix the importer, then delete.
2. Trim `api/models.py` query types: for each candidate type, grep for usage; delete only zero-hit cases.
3. Delete in dependency order: shims first → implementations → Protocols → directories.

## Tests

- All existing tests pass after deletions.
- New regression test: `pytest.raises(ModuleNotFoundError)` on `import sandbox.code_intelligence`.
- New regression test: `pytest.raises(ImportError)` on `from sandbox.api.transport import SandboxTransport`.

## Exit criteria

- Build / ruff / tests green.
- `find backend/src/sandbox/code_intelligence -type f` returns empty (and the directory itself is removed).
- `grep -r "SandboxTransport" backend/src/` returns zero production hits.
- `sandbox/api/` contains only the verb modules + `_registry.py` + `models.py` + `raw_exec.py`.

## Risks

- A production import of a deleted surface slips through grep. Mitigation: `make build` + `make test` + ruff are the empirical bar; any ImportError is a regression that blocks the slice.
- Aggressive `models.py` trim deletes a type that the query-side `plugins-refactor.md` work still needs. Mitigation: zero-hit grep is the deletion rule, not "looks unused."
