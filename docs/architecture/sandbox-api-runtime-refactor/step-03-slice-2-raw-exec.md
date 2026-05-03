# Step 3 — Slice 2 — Public `sandbox.api.raw_exec`

**Goal.** Introduce the un-guarded primitive used only by runtime/setup/lifecycle/debug paths. Migrate today's bundle upload + lifecycle to import it. Agent tools and `code_intelligence/daemon/client.py` are explicitly *not* migrated yet.

**Depends on.** Step 2 / Slice 1.

## Files

### Add
- `backend/src/sandbox/api/raw_exec.py` — `def raw_exec(sid, cmd, ...) -> RawExecResult: return get_adapter(sid).exec(cmd, ...)`.
- `backend/src/sandbox/api/models.py` — start with `SandboxResultBase` and `RawExecResult` only. The rest of §1.6 lands in Slice 6.
- `test_sandbox/test_api/test_importer_allowlist.py` — AST-walk test (per parent doc §6: unit test, not custom ruff rule).

### Modify
- Today's runtime bundle upload path (currently in `sandbox/code_intelligence/daemon/`): import `sandbox.api.raw_exec` instead of `SandboxTransport`.
- Lifecycle paths that drive sandbox creation/setup: same migration.
- Debug paths that today reach for the raw transport: same migration.

## Implementation tasks

1. Implement `RawExecResult` per §1.6 (un-guarded; carries `exit_code`, `stdout`, `stderr`).
2. Implement `raw_exec` as a thin wrapper. No retry, no logging — those layer on later if needed.
3. Migrate the three caller categories above. Agent tools and `daemon/client.py` continue using the old transport — both code paths coexist.
4. Importer allowlist test: fail if `sandbox.api.raw_exec` is imported by anything outside this set:
   - `sandbox/runtime/{bundle,setup_orchestrator}.py` (will exist after Slice 3; allow-list these paths up front).
   - `sandbox/lifecycle/*`.
   - debug paths (enumerate explicitly).
   New entries to the set require an allowlist edit, not a silent expansion.

## Tests

- `test_importer_allowlist` covers `raw_exec`.
- Existing tests pass unchanged.

## Exit criteria

- Build / ruff / tests green.
- `grep -r "from sandbox.api.raw_exec" backend/src/` matches only allowlisted modules.
- Agent tool imports unchanged in this slice.

## Risks

- A migrated call site silently breaks because the old transport had a richer return shape. Mitigation: keep `RawExecResult` field-compatible with what callers actually used.
