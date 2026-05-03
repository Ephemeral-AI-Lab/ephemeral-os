# Sandbox API Adapter Migration Plan

**Date:** 2026-05-02
**Status:** Complete — Steps 1–11 implemented.
**Scope:** Establish three provider-neutral surfaces and remove all direct
`sandbox.daytona.*` imports from `tools/*` and `sandbox/code_intelligence/*`:

- `SandboxApi` — tool-facing, audit-aware I/O.
- `SandboxTransport` — raw provider primitives (no audit, no policy).
- `CodeIntelligenceApi` — tool-facing semantic queries.

Daytona becomes the only `SandboxTransport` implementation. CI internals
(LSP, indexing, mutations, overlay/audit) consume `SandboxTransport`
directly. Tools never see Daytona; CI never sees Daytona.

## Implementation status

| Step | Scope | Status |
|---|---|---|
| 1 | API Protocols + frozen dataclass models + errors + import-fence test | **Done** |
| 2 | `audit.py` + `attribution.py` relocated; temporary `tools/core/{sandbox_commit,ci_attribution}.py` compat shims added | **Done** |
| 3 | `DaytonaTransport` (16 contract tests) | **Done** |
| 4 | `AuditedSandboxApi` (13 dispatch tests) | **Done** |
| 5 prep | `CheckedWriteSpec.content` widened to `bytes \| None` (deletes); `SandboxTransport.read_bytes_batch` added; staged-payload support for `apply_diff_batch_checked` | **Done** |
| 5.1 | `language_server/transport.py` + `LspClient` + `CodeIntelligenceService` + `registry` accept `transport=` kwarg (2 transport-path tests) | **Done (additive)** |
| 5.2 | `OverlayCaptureRunner` + `git_snapshot` + `AuditedCommandExecutor` accept `transport=` kwarg | **Done (additive)** |
| 5.3 | `file_discovery.py` + `SymbolIndex` accept `transport=` kwarg | **Done (additive)** |
| 5.4 | `ContentManager` accepts `transport=` kwarg with branches for `read`/`read_many`/`write`/`delete`/`apply_many_with_base_check` | **Done (additive)** |
| 6 | `SvcCodeIntelligence` (13 contract tests) | **Done** |
| 7 | `ExecutionMetadata` extended with `sandbox_api`/`code_intelligence_api`/`sandbox_transport`; `lifecycle/workspace.py.ensure_code_intelligence_runtime` constructs `DaytonaTransport`+`AuditedSandboxApi`+`SvcCodeIntelligence` per context and passes transport into CI service construction | **Done** |
| 8 | `daytona_toolkit/` → `sandbox_toolkit/` rename + 8 tool rewrites | **Done** |
| 9 | `ci_toolkit/` rewrite (5 tools) | **Done** |
| 10 | Delete legacy modules | **Done** |
| 11 | Comprehensive import-fence test | **Done** |

**Verification:** `uv run pytest -q` → 1052 passed, 389 deselected. `uv run
ruff check backend/src backend/tests` → all checks passed.

## Completion Notes

- The rename landed as a big-bang cutover: `tools/daytona_toolkit/` was deleted
  and `tools/sandbox_toolkit/` has no compatibility alias.
- `delete_file` was removed from the tool surface in favor of `remove_file`.
- `tools/core/ci_adapter.py`, `tools/core/sandbox_commit.py`, and
  `tools/core/ci_attribution.py` were deleted.
- `SandboxService.code_intelligence_for(..., transport=...)` is now activated
  from `lifecycle/workspace.py` when the context prepares the provider-neutral
  runtime.
- `tools/sandbox_toolkit/*`, `tools/ci_toolkit/*`, and
  `sandbox/code_intelligence/*` are covered by the import-fence test.
- `_is_real_daytona_fs()` / `_is_real_sdk()` provider sniffs were removed from
  CI internals.

## Why Steps 1–7 landed additively

The plan's "Eleven PRs" structure, plus the autopilot constraint of
landing each step without breaking the 167 existing tool tests or 325+
sandbox tests, drove an additive approach:

- Constructor signatures **add** `transport=` kwargs rather than
  replacing `sandbox=`.
- `tools/core/{sandbox_commit,ci_attribution}.py` temporarily became
  **shims** so early API relocation did not break existing callers.
- `ExecutionMetadata` **adds** three new fields rather than removing
  the legacy `daytona_sandbox` / `ci_service`.
- Legacy code paths inside CI internals stay alive alongside the new
  transport-aware paths.

Steps 8-11 completed that cutover: tool callers moved to the provider-neutral
surface, temporary shims were deleted, and the import fence now protects the
new boundary.

## Goals

1. **Provider-neutral I/O for tools.** `tools/sandbox_toolkit/*` depends on
   the `SandboxApi` protocol and its request/result models.
2. **Provider-neutral semantic queries for tools.** `tools/ci_toolkit/*`
   depends on the `CodeIntelligenceApi` protocol and its request/result models.
3. **Provider-neutral CI internals.** `sandbox/code_intelligence/*` depends
   on the provider-neutral sandbox API/transport helpers. The 5 modules that
   previously imported `sandbox.daytona.*` are refactored. The
   `_is_real_daytona_fs()` runtime type-sniff is deleted.
4. **CI work fully encapsulated from tools.** OCC, change tracking, audit,
   and attribution are internals of `SandboxApi`. Tools see audit results
   as fields on response models, not as a separate service dependency.

After this phase, `tools/*` and `sandbox/code_intelligence/*` do not import
`sandbox.daytona.*`. Daytona-specific imports stay inside provider/runtime
factory code. Adding a second provider becomes a matter of writing a new
`SandboxTransport` implementation; tools and CI continue to use the same
provider-neutral surfaces.

## Non-Goals

- In-sandbox CI sidecar daemon (Phase 2 — see roadmap).
- A second `SandboxTransport` implementation (Phase 3).
- Replacing `SymbolIndex`, the LSP backend, or the diagnostics engine.

## Architecture

Three layers, each with a distinct responsibility:

```text
                                                          ┌── SandboxApi (audit-aware)
                                                          │     • tools/sandbox_toolkit/*
                                                          │     • tools see audit metadata via results
tools/sandbox_toolkit/* ──→ SandboxApi  ──┬───────────────┤
                                          │               ↓
                                          │         SandboxTransport (raw primitives)
                                          │           • exec, read_bytes, write_bytes,
                                          │             apply_diff_batch_checked, search
sandbox/code_intelligence/* ──────────────┘               │
                                                          ├── DaytonaTransport (only impl in phase 1)
                                                          └── ModalTransport / DockerTransport (Phase 3+)

tools/ci_toolkit/* ──→ CodeIntelligenceApi ──→ CodeIntelligenceService ──→ SandboxTransport
                                                  (still backend-hosted in phase 1)
```

Why three layers, not two:

- **`SandboxApi` and CI have different needs over the same provider.** Tools
  want audited mutations. CI is the *producer* of audit signals; it needs
  raw primitives without an audit wrapper, otherwise it would be calling
  itself.
- **`SandboxTransport` is the single point of provider coupling.** Adding a
  new provider means writing one new `SandboxTransport` impl. Both tools
  (via `SandboxApi`) and CI (directly) get the new provider for free.
- This is **not** the rejected "Service → Adapter → ProviderService" shape
  where each layer mirrored the same method signatures. Each layer here
  has a different purpose: tool contract / audit policy / raw transport.

`SandboxApi` (audit-aware) is composed from `SandboxTransport` plus an
audit/OCC engine plus an attribution resolver. `CodeIntelligenceApi` is a
thin async wrapper over the existing backend-hosted
`CodeIntelligenceService`, which itself now consumes `SandboxTransport`.

## Target Folder Structure

```text
backend/src/sandbox/
  api/
    __init__.py
    sandbox_api.py             # SandboxApi Protocol
    transport.py               # SandboxTransport Protocol (raw primitives)
    code_intelligence_api.py   # CodeIntelligenceApi Protocol
    models.py                  # request/result types for all three APIs
    errors.py
    audit.py                   # THIN FACADE Protocol — forwards to OCC engine in code_intelligence/
                               # NOT a relocation of the OCC machinery
    attribution.py             # actor/run/task resolution
    registry.py                # selects SandboxTransport + provider bootstrap; Daytona by default

  daytona/
    __init__.py
    transport.py               # DaytonaTransport implements SandboxTransport
    bootstrap.py               # Daytona-specific CI provisioning (Phase 2 entry point)
    client.py                  # sync/async Daytona client ownership
    files.py                   # internal helpers used by transport.py
    process.py                 # exec, process handle, kill, tail
    search.py                  # grep/glob scripts and parsing
    scripts.py                 # uploaded script helpers
    paths.py

  lifecycle/                   # existing; rename DaytonaContextPreparer → SandboxContextPreparer
    service.py                 # CodeIntelligenceService factory; injects SandboxTransport
    context.py
    workspace.py                # CI bootstrap orchestrator: ensure_ready / ensure_built;
                                # in Phase 2, also drives provider-specific provisioning

  code_intelligence/           # existing OCC + LSP + indexing engine; STAYS HERE
                               # only its bottom-edge imports change to SandboxTransport
    service.py                 # CodeIntelligenceService facade (unchanged surface)
    language_server/
      transport.py             # uses SandboxTransport.exec
    mutations/                 # OCC ENGINE: WriteCoordinator, MutationService, Arbiter,
      content_manager.py       # Patcher, TimeMachine, ContentManager — engine stays put;
      arbiter.py               # only imports change to SandboxTransport
      patcher.py
      time_machine.py
      write_coordinator/
      mutation_service.py
    indexing/
      file_discovery.py        # uses SandboxTransport.search / read_bytes
      symbol_index.py
    overlay/                   # capture machinery: OverlayCaptureRunner, git_snapshot
      git_snapshot.py          # stays put; imports change to SandboxTransport
      capture_runner.py
    shell_command_executor.py  # temporary legacy shell/OCC projection layer
    core/
      ...

backend/src/tools/
  sandbox_toolkit/             # renamed from daytona_toolkit; no compat alias
    __init__.py
    registry.py                # make_sandbox_tools()
    read_file.py
    write_file.py
    edit_file.py
    remove_file.py
    move_file.py
    grep.py
    glob.py
    shell.py
    _file_tool_helpers.py
    _shell_prehooks.py
```

Modules deleted by this migration:

- `backend/src/tools/daytona_toolkit/` — renamed to `sandbox_toolkit/`.
- `backend/src/tools/daytona_toolkit/_mutation_helpers.py` — `ci_write_guard`
  replaced by `WriteFileResult.success` checks.
- `backend/src/tools/core/ci_adapter.py`.
- `backend/src/tools/core/sandbox_commit.py` — moved to `sandbox/api/audit.py`.
  This is a **thin tool-facing facade** (the file is small today and just wraps
  `lifecycle/commit.submit_commit` / `submit_shell_cmd`). The actual OCC engine
  in `code_intelligence/mutations/` and `code_intelligence/overlay/` does not move.
- `backend/src/tools/core/ci_attribution.py` — moved to `sandbox/api/attribution.py`.
- `_is_real_daytona_fs()` and similar runtime type-sniffs in
  `code_intelligence/mutations/content_manager.py:37`.
- `delete_file.py` (tool) — renamed to `remove_file.py`. No alias.

## Dependency Rules

```text
tools/sandbox_toolkit/*        → sandbox.api sandbox/tool models only
tools/ci_toolkit/*             → sandbox.api code-intelligence models only
sandbox/api/sandbox_api impl   → sandbox.api.transport, sandbox.api.audit, sandbox.api.attribution
sandbox/api/audit              → sandbox.api.transport
sandbox/code_intelligence/*    → sandbox.api transport/models/bash helpers (no sandbox.daytona.*)
sandbox/api/code_intelligence_api impl → sandbox.code_intelligence.* (still backend-hosted)
sandbox/daytona/*              → may use Daytona SDK
sandbox/lifecycle/workspace    → sandbox.daytona transport factory

NO tools/* or sandbox/code_intelligence/* module imports sandbox.daytona.*.
```

These rules are enforced by an import-fence test (Step 11 below).

## Core Interfaces

### `SandboxTransport`

Raw, provider-neutral primitives. No audit, no attribution, no policy. All
async; sync/async bridges live inside provider impls.

```python
@dataclass(frozen=True)
class RawExecResult:
    exit_code: int
    stdout: str
    stderr: str

@dataclass(frozen=True)
class CheckedWriteSpec:
    path: str
    content: bytes
    expected_sha: str | None        # None = create-only

@dataclass(frozen=True)
class CheckedWriteResult:
    success: bool
    written_paths: tuple[str, ...]
    conflict_paths: tuple[str, ...]
    conflict_reason: str | None

@dataclass(frozen=True)
class SearchMatch:
    path: str
    line: int
    column: int
    preview: str

class SandboxTransport(Protocol):
    name: str

    async def exec(
        self, sandbox_id: str, command: str, *,
        cwd: str | None = None, timeout: int | None = None,
    ) -> RawExecResult: ...

    async def read_bytes(self, sandbox_id: str, path: str) -> bytes: ...
    async def write_bytes(self, sandbox_id: str, path: str, content: bytes) -> None: ...

    async def apply_diff_batch_checked(
        self, sandbox_id: str, specs: Sequence[CheckedWriteSpec],
    ) -> CheckedWriteResult: ...

    async def search(
        self, sandbox_id: str, pattern: str, *,
        root: str | None = None, include: str | None = None,
    ) -> Sequence[SearchMatch]: ...

    async def list_paths(
        self, sandbox_id: str, glob: str, *, root: str | None = None,
    ) -> Sequence[str]: ...
```

### `SandboxApi`

Tool-facing, audit-aware. Composed from `SandboxTransport` + audit engine +
attribution resolver.

```python
class SandboxApi(Protocol):
    name: str

    async def read_file(self, sandbox_id: str, request: ReadFileRequest) -> ReadFileResult: ...
    async def write_file(self, sandbox_id: str, request: WriteFileRequest) -> WriteFileResult: ...
    async def edit_file(self, sandbox_id: str, request: EditFileRequest) -> EditFileResult: ...
    async def remove_file(self, sandbox_id: str, request: RemoveFileRequest) -> RemoveFileResult: ...
    async def move_file(self, sandbox_id: str, request: MoveFileRequest) -> MoveFileResult: ...
    async def grep(self, sandbox_id: str, request: GrepRequest) -> GrepResult: ...
    async def glob(self, sandbox_id: str, request: GlobRequest) -> GlobResult: ...
    async def shell(self, sandbox_id: str, request: ShellRequest) -> ShellResult: ...
```

### `CodeIntelligenceApi`

```python
class CodeIntelligenceApi(Protocol):
    name: str

    async def status(self, sandbox_id: str) -> WorkspaceStatus: ...
    async def query_symbols(self, sandbox_id: str, request: SymbolQueryRequest) -> SymbolQueryResult: ...
    async def find_references(self, sandbox_id: str, request: ReferencesRequest) -> ReferencesResult: ...
    async def diagnostics(self, sandbox_id: str, request: DiagnosticsRequest) -> DiagnosticsResult: ...
    async def workspace_structure(self, sandbox_id: str, request: WorkspaceStructureRequest) -> WorkspaceStructureResult: ...
```

Notes:

- All async even though the underlying `CodeIntelligenceService` query
  surface is sync; the bridge stays inside the API impl.
- No raw attribute access. Today's `_query_runtime.py:486` use of
  `svc.symbol_index` becomes a method on `CodeIntelligenceApi`.

## Request/Result Models

Tool-shaped, immutable, **carry audit metadata in results** so tools never
reach into `ci_service` to interpret outcomes.

```python
@dataclass(frozen=True)
class RequestActor:
    agent_id: str
    run_id: str = ""
    agent_run_id: str = ""
    task_id: str = ""

@dataclass(frozen=True)
class WriteFileRequest:
    path: str
    content: str
    actor: RequestActor

@dataclass(frozen=True)
class WriteFileResult:
    success: bool
    changed_paths: tuple[str, ...]
    conflict_reason: str | None    # non-None on OCC conflict

@dataclass(frozen=True)
class ShellRequest:
    command: str
    cwd: str | None = None
    timeout: int | None = None
    actor: RequestActor = ...

@dataclass(frozen=True)
class ShellResult:
    exit_code: int
    stdout: str
    stderr: str
    changed_paths: tuple[str, ...]
    ambient_changed_paths: tuple[str, ...]
    audit_success: bool
    audit_conflict_reason: str | None
```

## Tool-Layer Simplification

After migration, tools have two structural dependencies on
`ToolExecutionContext`:

- `context.sandbox_api: SandboxApi`
- `context.code_intelligence_api: CodeIntelligenceApi`

Example — write_file:

```python
async def write_file_tool(context, *, file_path, content):
    api = context.sandbox_api
    result = await api.write_file(
        context.sandbox_id,
        WriteFileRequest(path=file_path, content=content, actor=actor_from(context)),
    )
    if not result.success:
        return ToolResult(
            output=f"write_file: {result.conflict_reason or 'failed'}",
            is_error=True,
            metadata={"conflict": bool(result.conflict_reason)},
        )
    return _format_write_ok(file_path, result.changed_paths)
```

`actor_from(context)` is a small helper in `tools/core/sandbox_session.py`
that reads existing context fields. It does **not** import `ci_*`. The
`shell` tool drops from ~330 LOC to ~80.

## CI Internals Refactor (Phase 1, In Scope)

**OCC engine stays in `sandbox/code_intelligence/`.** The 7-class subsystem
that performs OCC and audit (`WriteCoordinator`, `MutationService`,
`Arbiter`, `Patcher`, `TimeMachine`, `ContentManager`,
`AuditedCommandExecutor`) is not relocated. Only its bottom-edge imports
flip from `sandbox.daytona.*` to `SandboxTransport`. OCC behavior,
sequencing, conflict detection, and on-disk audit semantics are unchanged.

`sandbox/api/audit.py` is a thin facade Protocol exposing the OCC engine
to `SandboxApi` callers. It does not re-implement OCC.

Each currently-coupled module is rewritten to consume `SandboxTransport`
instead of `sandbox.daytona.*`:

| Module | Before | After |
|---|---|---|
| `language_server/transport.py` | provider-specific bash command wrapping | uses `SandboxTransport.exec` |
| `mutations/content_manager.py` | provider-specific bash/file helpers for OCC writes | uses `SandboxTransport.apply_diff_batch_checked` + `read_bytes` |
| `overlay/git_snapshot.py` | provider-specific bash helpers for snapshot scripts | uses `SandboxTransport.exec` |
| `overlay/capture_runner.py` | provider-specific bash helpers for upperdir capture | uses `SandboxTransport.exec` |

**Runtime introspection deleted:**

- `code_intelligence/mutations/content_manager.py:37` — `_is_real_daytona_fs`
These exist today because CI didn't have an abstraction over the file
backend; with `SandboxTransport` typed as a Protocol, the parameter is
either a transport or it isn't. Tests pass a fake `SandboxTransport`
directly; no introspection needed.

`CodeIntelligenceService` itself stays backend-hosted. Its constructor
accepts a `SandboxTransport` (injected from the registry) instead of
reaching into Daytona modules.

## Async Behavior

All `SandboxApi`, `SandboxTransport`, and `CodeIntelligenceApi` methods are
async. Provider implementations may use sync SDK calls internally, but the
sync/async bridge is contained inside the implementation.

`run_sync` style bridges must not appear in tool code or in
`sandbox/code_intelligence/*` after this phase. They live only in
`sandbox/daytona/` and `sandbox/api/code_intelligence_api.py` impl.

## Implementation Steps

### Step 1 — Define API models and protocols

Create:

- `sandbox/api/models.py` (request/result types, `RequestActor`,
  `RawExecResult`, `CheckedWriteSpec/Result`, `SearchMatch`, error types).
- `sandbox/api/transport.py` (`SandboxTransport` Protocol).
- `sandbox/api/sandbox_api.py` (`SandboxApi` Protocol).
- `sandbox/api/code_intelligence_api.py` (`CodeIntelligenceApi` Protocol).
- `sandbox/api/errors.py`.

No implementation yet; this is the contract.

### Step 2 — Move CI internals out of `tools/core`

- `tools/core/sandbox_commit.py` → `sandbox/api/audit.py`. Audit engine
  composes `SandboxTransport`, not Daytona directly.
- `tools/core/ci_attribution.py` → `sandbox/api/attribution.py`. Adjust to
  accept/produce `RequestActor`.

Tools do not import from these new homes.

### Step 3 — Implement `DaytonaTransport`

Create `sandbox/daytona/transport.py` implementing `SandboxTransport`.
Compose existing helpers (`bash.py`, `exec_files.py`, `search_commands.py`,
etc.) — split where useful (`files.py`, `process.py`, `search.py`,
`scripts.py`) but keep splits surgical.

### Step 4 — Implement audit-aware `SandboxApi`

Create the canonical `SandboxApi` impl (one class — name it
`AuditedSandboxApi` for clarity) that takes a `SandboxTransport` + audit
engine + attribution resolver and produces audit-bearing results.

### Step 5 — Refactor `sandbox/code_intelligence/*` to consume `SandboxTransport`

Touch the 5 modules listed in the table above. Constructor signatures
change to accept a `SandboxTransport` parameter. Delete the
`_is_real_daytona_fs` helpers. **This is the largest single piece of
work in phase 1.** Land it in its own PR with full CI test coverage.

Suggested sub-order:

1. `language_server/transport.py` — smallest, exercises `exec`.
2. `overlay/capture_runner.py` and `overlay/git_snapshot.py` — exec-only, simple.
3. `indexing/file_discovery.py` — search + read_bytes; verify performance.
4. `mutations/content_manager.py` — OCC apply; highest risk, land last.

Existing `CodeIntelligenceService` tests are the regression net; they must
all still pass after each sub-step.

### Step 6 — Implement `SvcCodeIntelligence`

Create `sandbox/api/code_intelligence_api.py` impl as a thin async wrapper
over `CodeIntelligenceService`. Sync→async bridging via
`run_sync_in_executor` is contained here.

### Step 7 — Wire `ToolExecutionContext`

Add structural fields:

- `sandbox_api: SandboxApi`
- `code_intelligence_api: CodeIntelligenceApi`
- (internal) `sandbox_transport: SandboxTransport` — used by audit engine,
  attribution, and `CodeIntelligenceService` construction. Not read by tool
  code.

Existing `ci_service` and `daytona_sandbox` keys remain runtime internals for
constructing the provider-neutral surface, but tool code no longer reads them.
If new tool code needs sandbox or CI access, it must use `sandbox_api` or
`code_intelligence_api`.

### Step 8 — Rewrite `sandbox_toolkit`

Rename `tools/daytona_toolkit/` → `tools/sandbox_toolkit/`. Replace every
call into `sandbox.daytona.*`, `tools.core.ci_adapter`,
`tools.core.sandbox_commit`, and `tools.core.ci_attribution` with calls to
`context.sandbox_api`. Delete `_mutation_helpers.py`. Rename
`delete_file.py` → `remove_file.py`. No compat aliases.

### Step 9 — Rewrite `ci_toolkit` callers

Replace every `get_ci_service(context)` and `svc.X(...)` in `ci_toolkit/`
with `context.code_intelligence_api` and `await api.X(sandbox_id, request)`:

- `_query_runtime.py:328` → `context.code_intelligence_api`
- `_query_runtime.py:486` (`svc.symbol_index` raw access) → use a typed
  method on `CodeIntelligenceApi` that surfaces the actual data needed.
  **Do not** expose `symbol_index` as an attribute on the API.
- `_query_runtime.py:836` (`svc.query_symbols`) → `await api.query_symbols(...)`.
- `_query_runtime.py:908` (`svc.find_references`) → `await api.find_references(...)`.
- `ci_diagnostics.py:74` (`svc.diagnostics`) → `await api.diagnostics(...)`.
- `ci_workspace_structure.py`, `ci_status.py` → corresponding API methods.

### Step 10 — Delete legacy modules

After Step 8 and Step 9 land and tests pass:

- Delete `tools/core/ci_adapter.py`.
- Delete `tools/daytona_toolkit/` (no compat alias).
- Delete `tools/daytona_toolkit/_mutation_helpers.py`.
- Confirm `tools/core/sandbox_commit.py` and `tools/core/ci_attribution.py`
  no longer exist (they moved in Step 2).
- Confirm `_is_real_daytona_fs` is gone from
  `code_intelligence/mutations/content_manager.py`.

### Step 11 — Tests + import fence

**Import-fence test (mandatory):**

```python
def test_no_forbidden_daytona_imports():
    forbidden_for_tools = (
        "sandbox.daytona", "sandbox.code_intelligence",
        "tools.core.ci_adapter", "tools.core.sandbox_commit",
        "tools.core.ci_attribution",
    )
    forbidden_for_ci_internals = ("sandbox.daytona", "daytona_sdk")

    for module in iter_python_files("backend/src/tools/sandbox_toolkit"):
        assert_no_imports(module, forbidden_for_tools)
    for module in iter_python_files("backend/src/tools/ci_toolkit"):
        assert_no_imports(module, forbidden_for_tools)
    for module in iter_python_files("backend/src/sandbox/code_intelligence"):
        assert_no_imports(module, forbidden_for_ci_internals)
```

**Contract tests:**

- `SandboxTransport` against an in-memory fake; covers exec, read/write,
  apply_diff_batch_checked, search, and list_paths.
- `SandboxApi` against fake transport + real audit engine; covers audit
  metadata population.
- `CodeIntelligenceApi` against fake `CodeIntelligenceService`.
- `DaytonaTransport` integration tests for parity with the pre-migration
  Daytona helpers.

Focused verification:

```bash
uv run pytest backend/tests/test_sandbox -q
uv run pytest backend/tests/test_tools/test_sandbox_toolkit -q
uv run pytest backend/tests/test_tools/test_ci_toolkit -q
uv run ruff check backend/src/sandbox backend/src/tools backend/tests
```

## Cutover Criteria

Phase 1 is done when:

- `tools/sandbox_toolkit/*` imports only the provider-neutral sandbox API
  surface plus tool base utilities.
- `tools/ci_toolkit/*` imports only the provider-neutral code-intelligence API
  surface plus tool base utilities.
- `sandbox/code_intelligence/*` imports only provider-neutral sandbox API
  helpers (no `sandbox.daytona.*`, no `daytona_sdk`).
- `sandbox/api/*` does not import `sandbox.daytona.*` (registry/factory
  excepted).
- `tools/core/ci_adapter.py`, `tools/core/sandbox_commit.py`, and
  `tools/core/ci_attribution.py` are deleted.
- `tools/daytona_toolkit/` is deleted; no compat alias exists.
- `_is_real_daytona_fs()` is deleted from CI internals.
- `delete_file` is removed in favor of `remove_file`.
- The import-fence test passes.
- All existing test suites pass with no behavior changes beyond audit-metadata
  surfacing.

## Risks

1. **CI behavior regression from threading `SandboxTransport` through
   `code_intelligence/*`.** Five modules change constructor signatures.
   Mitigation: land each sub-step from Step 5 in its own PR with full CI
   test runs; sub-order from low-risk (LSP transport) to high-risk
   (mutations/content_manager).

2. **Performance regression in indexing.** `file_discovery.py` currently
   uses Daytona `FileDownloadRequest` for bulk file reads; the
   `SandboxTransport.read_bytes` per-call contract may be slower if
   misimplemented. Mitigation: benchmark indexing pre/post; if regression,
   add `SandboxTransport.read_bytes_batch` rather than papering over with
   threading.

3. **Sync→async bridge regressions for `CodeIntelligenceApi`.** Mitigation:
   contain the bridge in `SvcCodeIntelligence`; assert via contract
   tests that no tool code awaits a sync object.

5. **Larger blast radius than incremental approaches.** This phase deletes
   modules and renames packages atomically. Mitigation: PR boundaries —
   models/protocols (Step 1), audit relocation (Step 2), `DaytonaTransport`
   (Steps 3–4), CI refactor sub-steps (Step 5), `CodeIntelligenceApi`
   (Step 6), context wiring (Step 7), tool rewrites (Steps 8–9), deletions
   (Step 10), tests (Step 11). Eleven PRs, import-fence enforced from
   Step 8 onward.

## Out-of-Scope Future Phases

For visibility, not part of this plan:

- **Phase 2 — In-sandbox CI sidecar (Reading B: persistent daemon, not
  per-query scripts).** A long-running CI daemon runs inside each sandbox.
  It owns the symbol index, hosts LSP servers, performs OCC mutations, and
  exposes an RPC interface. The backend `CodeIntelligenceService` becomes
  a thin RPC client.

  **Runtime path.** Mechanically, this is a *swap* of one internal
  implementation: `CodeIntelligenceService` keeps its public surface but
  forwards to the sidecar over RPC. Because Phase 1 already routes CI
  internals through `SandboxTransport`, the sidecar option becomes a
  matter of implementing one more transport-style abstraction — either as
  a new `SandboxTransport` impl that proxies to the sidecar, or as a
  parallel `SidecarRpcClient` that `CodeIntelligenceService` swaps in.

  **Provisioning path.** The sandbox is responsible for *preparing itself*
  with everything CI needs to run inside it: sidecar daemon binary,
  language servers per language, tree-sitter parsers, symbol-index seed,
  warmed caches. Ownership and sequencing:

  1. **`sandbox/lifecycle/workspace.py` is the orchestrator.** It already
     hosts CI bootstrap calls today (`lsp_client.ensure_ready`,
     `symbol_index.ensure_built`). Phase 2 extends this hook to also start
     the sidecar daemon and verify readiness before the sandbox is
     exposed to tools or queries.
  2. **Per-provider `bootstrap.py` modules** know how to provision their
     specific sandbox image. `sandbox/daytona/bootstrap.py` encapsulates
     "how to install LSP servers, tree-sitter parsers, and the sidecar
     daemon into a Daytona sandbox." A future `sandbox/modal/bootstrap.py`
     does the equivalent for Modal. Each provider's bootstrap is
     registered alongside its `SandboxTransport` impl.
  3. **`SandboxTransport` stays raw.** Bootstrap is a separate concern;
     it does not pollute the transport Protocol. The orchestrator calls
     `provider_bootstrap.ensure_ready(transport, sandbox_id)` using
     transport primitives.
  4. **Sequencing**: bootstrap completes (or fails fast) before tools or
     CI queries are served. Daemon liveness/readiness checks are part of
     the bootstrap contract.

  Image-building (baking LSP servers and tree-sitter parsers into the
  sandbox image at build time) is a per-provider optimization that lives
  outside the runtime bootstrap path; the bootstrap module describes the
  runtime contract, not the image build pipeline.

  **Anti-pattern explicitly avoided:** spawning a fresh script for each
  semantic query (`find_references`, `query_symbols`, `diagnostics`).
  Cold-start cost per query, fragile stdout parsing, and discarded LSP
  state make this unworkable for interactive use. Persistent process or
  bust.

- **Phase 3 — Second sandbox provider.** Add `ModalTransport` (or
  similar) next to `DaytonaTransport`, both implementing
  `SandboxTransport`. Because Phase 1 routes both `SandboxApi` and
  `CodeIntelligenceService` through `SandboxTransport`, both tools and CI
  pick up the new provider with no further changes. Registry/factory
  selection by configuration.

- **Phase 4 — Background shell migration.** Add process-handle plumbing when
  cancellation, tailing, and status streaming are implemented.
