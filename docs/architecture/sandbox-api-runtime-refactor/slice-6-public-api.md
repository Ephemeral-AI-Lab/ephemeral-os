# Slice 6 — Public `sandbox.api.{shell, write, edit, read}`

**Goal.** Expose the four public verbs. Migrate agent tools to one-line pass-throughs. Replace today's per-verb result dataclasses with the §1.6 hierarchy.

**Depends on.** Slices 4 and 5b.

## Files

### Add
- `backend/src/sandbox/api/read.py` — thin `cat` wrapper over `raw_exec`. Returns `ReadFileResult`. Reads stay direct (parent doc §4 out-of-scope: not through entrypoint).
- `backend/src/sandbox/api/shell.py` — single `adapter.exec` invoking entrypoint with verb `shell`; returns `ShellResult`.
- `backend/src/sandbox/api/write.py` — verb `write` → `write_pipeline`; returns `WriteFileResult`.
- `backend/src/sandbox/api/edit.py` — verb `edit` → `edit_pipeline`; returns `EditFileResult`.

### Modify
- `backend/src/sandbox/api/models.py`: complete the §1.6 hierarchy — `SandboxResultBase`, `GuardedResultBase`, `ConflictInfo`, `ReadFileResult`, `RawExecResult`, `WriteFileResult`, `EditFileResult`, `ShellResult`. All frozen + kw_only.
- Agent tools become trivial pass-throughs:
  - `backend/src/tools/sandbox_toolkit/shell.py`
  - `backend/src/tools/sandbox_toolkit/write_file.py`
  - `backend/src/tools/sandbox_toolkit/edit_file.py`
  - `backend/src/tools/sandbox_toolkit/read_file.py`
- `test_importer_allowlist`: extend so agent tools may import only `sandbox.api.{shell, read, write, edit}` — never `raw_exec`, never `_registry`, never `providers`.

### Delete
- Today's `OperationResult` (under OCC).
- Overlay `SimpleNamespace` builders.
- `sandbox/code_intelligence/mutations/mutation_results.py` shape-specific helpers (the dataclass surface is now §1.6).

## Implementation tasks

1. Land the §1.6 result types. Run a global migration of every constructor call. Frozen + kw_only ensures ruff/typing flags missing or extra fields at import time.
2. Implement the four verb modules. Each is small: serialize op envelope → `adapter.exec` → parse JSON result → return typed result. **One wire trip per call.**
3. Migrate agent tools. Diff per tool ≤10 lines: import the verb module, pass args through, return the result. No business logic in the agent tool.
4. Update the importer allowlist for new public surfaces. Also lock down: `raw_exec` continues to be unreachable from agent paths.
5. Audit `gitinclude_changed_paths` / `gitignore_changed_paths` plumbing across `WriteFileResult`, `EditFileResult`, `ShellResult` — same shape per §1.6, populated at the pipeline boundary.

## Tests

- New `test_sandbox/test_api/test_shell.py`, `test_write.py`, `test_edit.py`, `test_read.py` — one wire trip per call; result-type correctness; `ConflictInfo` populated on guard rejection.
- Updated `test_importer_allowlist` covers all four public verbs and the agent-tool restrictions.
- Existing agent-tool tests pass with no logic change (only import-path updates).

## Exit criteria

- Build / ruff / tests green.
- Agent tools import only `sandbox.api.{shell, read, write, edit}`. Lint allowlist test enforces this.
- `OperationResult`, overlay `SimpleNamespace` builders, and `mutation_results.py` shape helpers are gone.
- §1.6 result types are the only result surface across guarded verbs; hierarchy makes `ConflictInfo` unreachable from `ReadFileResult` / `RawExecResult`.

## Risks

- Result-type migration touches many call sites at once. Mitigation: `frozen=True, kw_only=True` makes ruff/mypy catch missing or extra fields at import.
- Wire shape changes silently between old per-verb dataclass and new §1.6 type. Mitigation: each verb's test asserts exactly one `adapter.exec` and round-trips the typed result.
