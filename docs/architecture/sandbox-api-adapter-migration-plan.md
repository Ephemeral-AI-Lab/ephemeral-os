# Sandbox API Adapter Migration Plan

**Date:** 2026-05-02
**Status:** In progress — Steps 1–7 of 11 complete (additive, backward-compatible).
Steps 8–11 (tool rewrites, deletions, comprehensive import-fence) pending.
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
| 2 | `audit.py` + `attribution.py` relocated; `tools/core/{sandbox_commit,ci_attribution}.py` rewritten as compat shims | **Done** |
| 3 | `DaytonaTransport` (16 contract tests) | **Done** |
| 4 | `AuditedSandboxApi` (13 dispatch tests) | **Done** |
| 5 prep | `CheckedWriteSpec.content` widened to `bytes \| None` (deletes); `SandboxTransport.read_bytes_batch` added; staged-payload support for `apply_diff_batch_checked` | **Done** |
| 5.1 | `language_server/transport.py` + `LspClient` + `CodeIntelligenceService` + `registry` accept `transport=` kwarg (2 transport-path tests) | **Done (additive)** |
| 5.2 | `OverlayAuditor` + `git_snapshot` + `AuditedCommandExecutor` accept `transport=` kwarg | **Done (additive)** |
| 5.3 | `file_discovery.py` + `SymbolIndex` accept `transport=` kwarg | **Done (additive)** |
| 5.4 | `ContentManager` accepts `transport=` kwarg with branches for `read`/`read_many`/`write`/`delete`/`apply_many_with_base_check` | **Done (additive)** |
| 6 | `SvcCodeIntelligence` (13 contract tests) | **Done** |
| 7 | `ExecutionMetadata` extended with `sandbox_api`/`code_intelligence_api`/`sandbox_transport`; `lifecycle/workspace.py.ensure_code_intelligence_runtime` constructs `DaytonaTransport`+`AuditedSandboxApi`+`SvcCodeIntelligence` per context (3 wiring tests) | **Done (partial activation)** |
| 8 | `daytona_toolkit/` → `sandbox_toolkit/` rename + 8 tool rewrites | **Pending** |
| 9 | `ci_toolkit/` rewrite (5 tools) | **Pending** |
| 10 | Delete legacy modules | **Pending** |
| 11 | Comprehensive import-fence test | **Pending** |

**Test surface:** 549 passing (167 tools + 382 sandbox = +60 net new test
assertions across 5 new test files; 0 regressions in pre-existing tests).

## Deferred items (Steps 1–7 left these in place)

These items are intentionally not yet acted on — Steps 8–11 will resolve
each. Reviewers should treat each as a **known-pending cleanup**, not a
bug to file or fix in isolation.

### Compatibility shims still alive

These exist so `daytona_toolkit/*` keeps working through Steps 8–9
without per-tool migration:

- `tools/core/sandbox_commit.py` — thin shim re-exporting `CommitOp`,
  `FileChangeResult`, `commit_metadata`, `failure_status` from
  `sandbox.api.audit`, plus context-aware
  `submit_commit_from_context` / `submit_shell_cmd_from_context`
  wrappers. **Step 10 deletes this file.**
- `tools/core/ci_attribution.py` — shim re-exporting `AgentAttribution`
  and the context-aware `agent_attribution_from_context` /
  `rebind_ci_service` / `resolved_agent_id` helpers, plus
  `actor_from_context` adapter. **Step 10 deletes this file.**
- `tools/core/ci_adapter.py` — untouched; still imported by 5
  `daytona_toolkit` tools and the `ci_toolkit` modules. **Step 10
  deletes this file.**

### Step 5 transport branches: dormant in production

Step 5 sub-steps added `transport=` parameters to every CI internal
constructor and threaded transport-aware code paths into each module.
**Production wiring (Step 7) currently does *not* pass a transport into
`SandboxService.code_intelligence_for`**, so:

- `CodeIntelligenceService` continues to construct `LspClient`,
  `SymbolIndex`, `ContentManager`, and `AuditedCommandExecutor` with
  `transport=None`, which means every transport-aware branch falls
  through to the legacy `self._sandbox` path.
- The `AuditedSandboxApi` and `SvcCodeIntelligence` wrappers attached to
  the context **do** consume the new `DaytonaTransport`, but they
  delegate writes/edits/etc. through the engine `svc` which is itself
  still on the legacy path.
- Net effect: **production behavior is unchanged.** All Step 5 transport
  paths are exercised only by isolated contract tests.

A separate sub-step ("Step 7.5") needs to extend
`SandboxService.code_intelligence_for` to also pass the constructed
transport into `get_code_intelligence`. This is a one-line change but
flips the engine onto the new code paths — a real production behavior
change that should land as its own reviewable PR.

### Per-module deferrals inside Step 5 sub-steps

- **`language_server/transport.py`** — legacy `self._sandbox` branch
  preserved alongside the new transport branch. The
  `from sandbox.daytona.bash import _wrap_bash_command, _extract_exit_code`
  import remains for the legacy branch. Step 11 cleanup will remove
  both once tests migrate to constructing `LspClient(transport=...)`.
- **`overlay/auditor.py` + `overlay/git_snapshot.py`** — same pattern;
  legacy `exec_process` callback path stays active. Daytona imports
  remain.
- **`indexing/file_discovery.py`** — `_is_real_sdk(fs)` sniff retained;
  legacy `fs.search_files`/`fs.list_files`/`fs.download_files` paths
  retained. Step 11 deletes the sniff and the legacy paths.
- **`mutations/content_manager.py`** —
  - `_is_real_daytona_fs` sniff still present at line 37 (deletion
    deferred to Step 11; no test currently relies on it but production
    code still calls into the branch when `transport is None`).
  - `apply_many` (non-checked batch) has **no transport branch** — the
    `SandboxTransport` Protocol does not yet expose an unchecked batch
    apply primitive. Falls through to legacy `_apply_remote_batch`.
  - `list_folder_files` has **no transport branch** — uses
    `transport.list_paths` could replace it but the script semantics
    differ (folder enumeration vs glob match). Deferred.

### Test cascade not yet performed

Existing tests construct `LspClient(workspace_root=..., sandbox=...)`,
`CodeIntelligenceService(sandbox_id=..., sandbox=...)`, and
`ContentManager(workspace_root, sandbox=...)` with sandbox handles, not
transports. **All 25+ LSP tests, all overlay tests, and all
ContentManager tests still pass `sandbox=`** because the legacy paths
remain. Step 11 (or a dedicated test-migration sub-step) is the place
to flip them.

### Per-class `NotImplementedError` markers

Two Phase 2-bound surfaces raise `NotImplementedError` with explanatory
messages pointing to the in-sandbox sidecar daemon:

- `DaytonaTransport.start_process` — long-running process handles
  arrive with the sidecar; today's LSP and shell tools use one-shot
  `exec`.
- `AuditedSandboxApi.shell_background` — same Phase 2 reasoning;
  background shell needs a `ProcessHandle`.

These are intentional and part of the ProcessHandle Protocol contract
(plan Risk #4 was about defining the contract upfront so the sidecar
can plug in cleanly later).

### Two implementations of OCC checked-batch-apply

`DaytonaTransport.apply_diff_batch_checked` and
`ContentManager._apply_remote_batch_checked` both build the same kind of
inline+staged Python script for OCC writes. They diverged in Step 5
prep (transport added staging support; ContentManager already had it).
**Step 11 should unify them** — extract a shared helper in
`sandbox/daytona/` (or move ContentManager's apply path to call
`SandboxTransport.apply_diff_batch_checked`). Both implementations are
verified equivalent by the existing OCC test suite.

## Open design questions for Steps 8–11

1. **Step 8 rename topology.** The plan says "no compat alias" for the
   `daytona_toolkit/` → `sandbox_toolkit/` rename. Two interpretations:
   - **Big-bang:** delete `daytona_toolkit/` in the same change as
     creating `sandbox_toolkit/`. ~30 file changes (8 tools + tests +
     fixtures + registry). High regression risk.
   - **Stage:** create `sandbox_toolkit/` alongside `daytona_toolkit/`,
     migrate tools one at a time, swap the registry, then delete
     `daytona_toolkit/` in Step 10. Lower risk per change but creates a
     transient duplicate package.

   Recommend **stage** for safety; the "no compat alias" rule is about
   the end state, not the transition.

2. **Production transport activation timing (Step 7.5).** Should the
   one-line change to `SandboxService.code_intelligence_for` (adding
   `transport=transport`) land before or after Step 8?
   - **Before:** Step 5 transport branches start running in production
     immediately. Any Step 5 sub-step bug surfaces in CI runs.
   - **After:** Step 8 tools migrate first; activation happens with
     more test coverage. Lower risk but delays the actual benefit.

   Recommend **after Step 8** so the new code paths see real tool
   traffic in tests before becoming load-bearing.

3. **Test migration strategy.** When Step 11 lands the comprehensive
   import-fence, every existing test that constructs `LspClient`,
   `ContentManager`, etc. with `sandbox=` will fail unless either:
   - Tests are migrated en masse to construct with `transport=`.
   - The fence allows the legacy `sandbox=` constructor parameters but
     forbids `from sandbox.daytona.*` imports inside the engine modules.

   Option 2 is more incremental but leaves the engine modules with
   dead `if self._sandbox:` branches the Protocol contract claims are
   gone.

## Why Steps 1–7 landed additively (not as a clean refactor)

The plan's "Eleven PRs" structure, plus the autopilot constraint of
landing each step without breaking the 167 existing tool tests or 325+
sandbox tests, drove an additive approach:

- Constructor signatures **add** `transport=` kwargs rather than
  replacing `sandbox=`.
- `tools/core/{sandbox_commit,ci_attribution}.py` become **shims** that
  re-export, not deleted modules.
- `ExecutionMetadata` **adds** three new fields rather than removing
  the legacy `daytona_sandbox` / `ci_service`.
- Legacy code paths inside CI internals stay alive alongside the new
  transport-aware paths.

Step 11 is where the cleanup happens: legacy paths deleted, fence
enforced, dead branches stripped.

## Goals

1. **Provider-neutral I/O for tools.** `tools/sandbox_toolkit/*` depends only
   on `SandboxApi`.
2. **Provider-neutral semantic queries for tools.** `tools/ci_toolkit/*`
   depends only on `CodeIntelligenceApi`.
3. **Provider-neutral CI internals.** `sandbox/code_intelligence/*` depends
   only on `SandboxTransport`. The 5 modules that currently import
   `sandbox.daytona.*` are refactored. The `_is_real_daytona_fs()` runtime
   type-sniff is deleted.
4. **CI work fully encapsulated from tools.** OCC, change tracking, audit,
   and attribution are internals of `SandboxApi`. Tools see audit results
   as fields on response models, not as a separate service dependency.

After this phase, the only modules that import `sandbox.daytona.*` are
inside `sandbox/daytona/` itself plus a single registry/factory. Adding a
second provider becomes a matter of writing a new `SandboxTransport` impl;
both tools and CI work for free.

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
                                          │             apply_diff_batch_checked, search,
                                          │             start_process
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
    recovery.py
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
      transport.py             # uses SandboxTransport.start_process / exec
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
    overlay/                   # AUDIT MACHINERY: AuditedCommandExecutor, git_snapshot,
      git_snapshot.py          # auditor — also stays put; imports change to SandboxTransport
      auditor.py
      command_executor.py
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
  `code_intelligence/mutations/content_manager.py:37` and
  `code_intelligence/indexing/file_discovery.py:184`.
- `delete_file.py` (tool) — renamed to `remove_file.py`. No alias.

## Dependency Rules

```text
tools/sandbox_toolkit/*        → sandbox.api.sandbox_api ONLY
tools/ci_toolkit/*             → sandbox.api.code_intelligence_api ONLY
sandbox/api/sandbox_api impl   → sandbox.api.transport, sandbox.api.audit, sandbox.api.attribution
sandbox/api/audit              → sandbox.api.transport
sandbox/code_intelligence/*    → sandbox.api.transport ONLY (no sandbox.daytona.*)
sandbox/api/code_intelligence_api impl → sandbox.code_intelligence.* (still backend-hosted)
sandbox/daytona/*              → may use Daytona SDK
sandbox/api/registry           → sandbox.daytona (the single factory site)

NO other module imports sandbox.daytona.*.
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

class ProcessHandle(Protocol):
    process_id: str
    async def write_stdin(self, data: bytes) -> None: ...
    async def read_stdout(self, n: int = -1) -> bytes: ...
    async def read_stderr(self, n: int = -1) -> bytes: ...
    async def status(self) -> ProcessStatus: ...
    async def kill(self) -> None: ...
    async def wait(self) -> int: ...

class SandboxTransport(Protocol):
    name: str

    async def exec(
        self, sandbox_id: str, command: str, *,
        cwd: str | None = None, timeout: int | None = None,
    ) -> RawExecResult: ...

    async def start_process(
        self, sandbox_id: str, command: str, *,
        cwd: str | None = None,
    ) -> ProcessHandle: ...

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
    async def shell_background(self, sandbox_id: str, request: ShellRequest) -> ProcessHandle: ...
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

| Module | Today | After |
|---|---|---|
| `language_server/transport.py:11` | imports `sandbox.daytona.bash` for LSP process plumbing | uses `SandboxTransport.start_process` + `ProcessHandle` stdin/stdout |
| `mutations/content_manager.py:16,20` | imports `sandbox.daytona.bash`, `sandbox.daytona.exec_files` for OCC writes | uses `SandboxTransport.apply_diff_batch_checked` + `read_bytes` |
| `indexing/file_discovery.py:12,95` | imports `sandbox.daytona.bash` and `daytona_sdk.common.filesystem` | uses `SandboxTransport.search` + `list_paths` + `read_bytes` |
| `overlay/git_snapshot.py:36` | imports `sandbox.daytona.bash` for snapshot scripts | uses `SandboxTransport.exec` |
| `overlay/auditor.py:57` | imports `sandbox.daytona.bash` for change-tracking | uses `SandboxTransport.exec` |

**Runtime introspection deleted:**

- `code_intelligence/mutations/content_manager.py:37` — `_is_real_daytona_fs`
- `code_intelligence/indexing/file_discovery.py:184` — `_is_real_daytona_fs`

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

## Long-Running Shell

`SandboxApi.shell` is bounded. `SandboxApi.shell_background` returns a
`ProcessHandle` (the same Protocol used by `SandboxTransport.start_process`
and by `language_server/transport.py`). One handle protocol across LSP,
sandbox-tool background shell, and any future daemon use.

## Implementation Steps

### Step 1 — Define API models and protocols

Create:

- `sandbox/api/models.py` (request/result types, `RequestActor`,
  `RawExecResult`, `CheckedWriteSpec/Result`, `SearchMatch`,
  `ProcessStatus`, error types, `ProcessHandle`).
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

1. `language_server/transport.py` — smallest, exercises `start_process`.
2. `overlay/auditor.py` and `overlay/git_snapshot.py` — exec-only, simple.
3. `indexing/file_discovery.py` — search + read_bytes; verify performance.
4. `mutations/content_manager.py` — OCC apply; highest risk, land last.

Existing `CodeIntelligenceService` tests are the regression net; they must
all still pass after each sub-step.

### Step 6 — Implement `DaytonaCodeIntelligence`

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

Existing `ci_service`, `daytona_sandbox`, `ci_sandbox` keys are removed
from the public context surface; if anything outside the API/transport
layer still reads them, fix that caller.

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
- `_query_runtime.py:458` (`svc.status()`) → `await api.status(...)`
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
  `code_intelligence/mutations/content_manager.py` and
  `code_intelligence/indexing/file_discovery.py`.

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
  apply_diff_batch_checked, search, list_paths, start_process.
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

- `tools/sandbox_toolkit/*` imports only `sandbox.api.sandbox_api` and tool
  base utilities.
- `tools/ci_toolkit/*` imports only `sandbox.api.code_intelligence_api`.
- `sandbox/code_intelligence/*` imports only `sandbox.api.transport` (no
  `sandbox.daytona.*`, no `daytona_sdk`).
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
   contain the bridge in `DaytonaCodeIntelligence`; assert via contract
   tests that no tool code awaits a sync object.

4. **`ProcessHandle` Protocol must serve LSP, background shell, and future
   sidecar-RPC equally well.** Mitigation: define the Protocol with
   bidirectional stdin/stdout streams in Step 1, validate against LSP use
   in Step 5 sub-step 1 before committing further.

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

- **Phase 4 — Background shell migration.** Replace the ad-hoc background
  shell wrapper with full `ProcessHandle`-based plumbing including
  cancellation, tailing, and status streaming exposed through tool-facing
  surfaces.
