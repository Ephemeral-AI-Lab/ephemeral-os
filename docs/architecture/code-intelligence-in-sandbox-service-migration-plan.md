# In-Sandbox Code Intelligence Service Migration Plan

**Date:** 2026-05-02
**Status:** Draft
**Scope:** Move code intelligence execution and state into each sandbox while
preserving the existing `SandboxApi`, `CodeIntelligenceApi`, OCC, and overlay
contracts.

## Goal

Today, `sandbox/code_intelligence` is packaged under `sandbox/`, but the
service still runs in the backend process. The backend-hosted
`CodeIntelligenceService` owns symbol indexing, LSP query caches, mutation
coordination, edit history, rollback snapshots, and the overlay shell auditor.
It reaches into the sandbox through `SandboxTransport`.

The target state is a long-running code-intelligence daemon inside each
sandbox. The daemon owns the code-intelligence service, persistent index/cache
storage, LSP processes, OCC mutation pipeline, and overlay shell execution.
The backend becomes a thin RPC client that implements the existing
`SandboxApi` and `CodeIntelligenceApi` protocols for tools.

## Non-Goals

- Do not change tool-facing names: `read_file`, `write_file`, `edit_file`,
  `remove_file`, `move_file`, `shell`, and `ci_*` tools keep the same contract.
- Do not replace `WriteCoordinator`, `MutationService`, `Arbiter`, or
  `OverlayAuditor` policy during this migration.
- Do not make per-query scripts for semantic queries. The daemon must be a
  persistent process so index, LSP, and cache state survive between calls.
- Do not put code-intelligence internal storage under the repository root.

## Current Runtime Shape

```text
tools/sandbox_toolkit/* -> SandboxApi
                         -> AuditedSandboxApi
                         -> backend CodeIntelligenceService
                         -> SandboxTransport
                         -> sandbox process/files

tools/ci_toolkit/*      -> CodeIntelligenceApi
                         -> SvcCodeIntelligence
                         -> backend CodeIntelligenceService
                         -> SandboxTransport
                         -> sandbox process/files
```

This gives tools a good protocol boundary, but state is still split across the
backend process and the sandbox. Large or long-lived state also crosses the
sandbox provider network layer: symbol index reads, file downloads, LSP query
scripts, checked apply payloads, overlay runtime uploads, and diff reads.

## Target Runtime Shape

```text
tools/sandbox_toolkit/* -> SandboxApi
                         -> SidecarSandboxApi RPC client
                         -> in-sandbox CI daemon
                         -> local CodeIntelligenceService
                         -> OCC / overlay / workspace

tools/ci_toolkit/*      -> CodeIntelligenceApi
                         -> SidecarCodeIntelligenceApi RPC client
                         -> in-sandbox CI daemon
                         -> SymbolIndex / LSP / cache stores
```

`SandboxTransport` remains provider-neutral, but after cutover it is only used
for daemon bootstrap, readiness checks, recovery, and explicit provider
operations. Agent-visible workspace mutations do not go through raw transport.

## Storage Contract

There are two storage domains with different rules.

### Workspace Storage

The workspace is the repository checkout and all user-visible files. Every
write/edit/update/remove/move/bash operation that can mutate this domain must
enter through the OCC gate:

- `write_file`
- `edit_file`
- `remove_file`
- `move_file`
- `shell`

Allowed mutation path:

```text
tool -> SandboxApi -> sidecar RPC -> CodeIntelligenceService
     -> MutationService / WriteCoordinator / OverlayAuditor
     -> workspace files
```

Forbidden mutation paths:

```text
tool -> SandboxTransport.write_bytes/apply_diff_batch_checked -> workspace
tool -> raw process.exec("bash ...") outside overlay -> workspace
sidecar RPC handler -> pathlib.write_text(...) directly under repo root
```

### CI Internal Storage

The code-intelligence daemon owns its internal state inside the sandbox but
outside the repository root. Internal storage is not user workspace state, so it
does not go through OCC and must not be classified by overlay as user work.

Recommended default:

```text
$HOME/.ephemeralos/code-intelligence/
  workspaces/
    <workspace_hash>/
      meta.json
      daemon.pid
      daemon.sock
      daemon.log
      symbol_index.sqlite
      file_fingerprints.sqlite
      lsp_cache.sqlite
      query_cache.sqlite
      edit_history.sqlite
      snapshots/
      overlay_runtime/
      tmp/
```

`<workspace_hash>` should be derived from the canonical workspace root and, if
available, the sandbox id. This keeps multiple checkouts in the same sandbox
from sharing stale index state.

### Store Responsibilities

| Store | Owner | Purpose | Persistence rule |
|---|---|---|---|
| `meta.json` | daemon bootstrap | schema version, sidecar version, workspace root, repo identity | rewrite atomically |
| `symbol_index.sqlite` | `SymbolIndex` | symbol rows by file, name, kind, line, signature, container | update after successful OCC commits and background reindex |
| `file_fingerprints.sqlite` | indexer | path, size, mtime, content hash, indexed generation | source of incremental index validity |
| `lsp_cache.sqlite` | `LspClient` | definition/reference/hover/diagnostic results keyed by query and file generation | TTL plus invalidation on changed file |
| `query_cache.sqlite` | `CodeIntelligenceApi` layer | workspace-structure and symbol-query result cache | invalidated by generation |
| `edit_history.sqlite` | `Arbiter` | durable edit ledger, conflicts, actor/run/task attribution | append transactionally after commit |
| `snapshots/` | `TimeMachine` | rollback snapshots for undo/diagnostics | capped by count and total bytes |
| `overlay_runtime/` | daemon bootstrap | extracted overlay runtime package, keyed by code hash | replace on runtime version change |
| `tmp/` | daemon operations | staged payloads and transient command material | cleaned on startup and after each op |

SQLite is preferred for the index, cache, and ledger stores because it gives
atomic local updates, cheap queries, and simple schema migration. JSONL is
acceptable only for append-only logs where query performance is irrelevant.

## OCC And Overlay Invariants

These invariants must remain true before, during, and after migration.

1. `WriteCoordinator` remains the single authority for dedicated file
   mutations.
2. Dedicated mutations keep sorted per-file locks, exact-base checked apply,
   fallback non-overlapping merge, rollback on batch failure, symbol refresh,
   and LSP invalidation.
3. `shell` remains fail-closed through overlay. It must not run raw bash against
   the live workspace.
4. Overlay gitinclude-route changes keep strict-base OCC and first-writer-wins.
5. Overlay gitignore-route writes keep direct-merge last-writer-wins semantics.
6. Mixed gitinclude/gitignore partial-apply behavior remains visible in the
   result metadata.
7. `.git` writes, unsupported symlinks, unsupported opaque dirs, and non-UTF-8
   gitinclude changes remain policy rejects.
8. CI internal storage paths are excluded from overlay tracking because they
   live outside the workspace root.
9. Any successful workspace mutation refreshes or invalidates CI stores inside
   the same daemon process before the RPC response returns.
10. Any failed or aborted OCC operation does not advance symbol/cache/edit
    stores, except for conflict telemetry.

## Sidecar API Boundary

The daemon should expose two protocol-shaped RPC surfaces.

### `SidecarSandboxApi`

Implements the existing `SandboxApi` contract:

- `read_file`
- `grep`
- `glob`
- `write_file`
- `edit_file`
- `remove_file`
- `move_file`
- `shell`

Read/search can use local filesystem access inside the sandbox. Mutation
methods must call the local `CodeIntelligenceService` and never perform direct
workspace writes.

### `SidecarCodeIntelligenceApi`

Implements the existing `CodeIntelligenceApi` contract:

- `status`
- `query_symbols`
- `find_references`
- `diagnostics`
- `workspace_structure`

These methods read from daemon-owned index/cache/LSP state. They should not
download workspace files through the provider network layer.

### Transport Shape

The first implementation can use a Unix domain socket inside the sandbox plus a
small backend RPC client that tunnels requests through the provider process
execution mechanism. A later provider can expose a direct forwarded port or
native process handle.

The RPC protocol should be request/response JSON with explicit method names,
schema versions, and error envelopes. Avoid stdout scraping from arbitrary
scripts; only the RPC shim may parse daemon responses.

## Bootstrap And Lifecycle

`sandbox/lifecycle/workspace.py` remains the orchestration point because it
already prepares code-intelligence runtime fields for tool contexts.

Bootstrap sequence:

1. Resolve canonical workspace root.
2. Build or reuse `SandboxTransport` for provider bootstrap.
3. Ensure sidecar files are installed inside the sandbox.
4. Start the daemon if it is not already running for this workspace.
5. Probe readiness over RPC.
6. Attach `SidecarSandboxApi` to `context["sandbox_api"]`.
7. Attach `SidecarCodeIntelligenceApi` to `context["code_intelligence_api"]`.
8. Keep `context["sandbox_transport"]` for bootstrap/recovery only.

Provider-specific installation belongs in provider bootstrap modules, for
example `sandbox/daytona/bootstrap.py`. The bootstrap module knows how to copy
the daemon bundle, start it, probe it, and recover it for that provider. It
does not own OCC policy.

## Migration Phases

### Phase 1: Add Durable Store Interfaces

- Add store interfaces for symbol index, file fingerprints, LSP cache, query
  cache, edit history, and snapshots.
- Implement SQLite-backed stores under the sidecar state root.
- Keep current in-memory implementations as test/local fallbacks.
- Add schema versioning and atomic migration helpers.

Verification:

- Store unit tests for create, reopen, update, invalidate, and schema mismatch.
- A service restart preserves indexed paths and edit history.

### Phase 2: Build The In-Sandbox Daemon Skeleton

- Add `sandbox/code_intelligence/sidecar/`.
- Add daemon entrypoint, workspace root validation, state root validation, lock
  file, socket path, readiness endpoint, and status endpoint.
- Construct local `CodeIntelligenceService` inside the daemon with no remote
  `transport`.
- Keep the backend path unchanged.

Verification:

- Bootstrap starts one daemon per sandbox/workspace.
- Readiness includes workspace root, sidecar version, schema version, and store
  paths.
- Restart reuses the same store directory.

### Phase 3: Move Read-Only CI Queries

- Implement `SidecarCodeIntelligenceApi`.
- Route `status`, `workspace_structure`, and `query_symbols` to the daemon.
- Persist and load `SymbolIndex` from `symbol_index.sqlite`.
- Add incremental reindex via `file_fingerprints.sqlite`.

Verification:

- `ci_workspace_structure` and symbol query tools work without remote batch
  downloads.
- Backend `SvcCodeIntelligence` remains available behind a feature flag for
  rollback.
- Index persists across daemon restart.

### Phase 4: Move LSP Queries And Caches

- Run Python/Jedi queries inside the daemon process.
- Persist cache entries in `lsp_cache.sqlite` keyed by query type, file path,
  line/character, file hash or generation, and sidecar version.
- Invalidate caches on successful mutations.

Verification:

- Definition/reference/hover/diagnostic queries reuse cache after restart when
  file generation matches.
- Cache entries are invalidated when a file changes through OCC.
- No per-query provider exec calls are made for normal LSP queries.

### Phase 5: Move Dedicated Mutations

- Implement `SidecarSandboxApi.write_file`, `edit_file`, `remove_file`, and
  `move_file`.
- RPC handlers call the daemon-local `MutationService`.
- Persist edit history and snapshots.
- Keep `WriteCoordinator` unchanged except for injected persistent stores.

Verification:

- Existing OCC tests pass against sidecar-backed APIs.
- Concurrent same-file writes still produce one commit and one
  `aborted_version`/conflict.
- Non-overlapping merge behavior is unchanged.
- Successful mutations refresh `symbol_index.sqlite` and invalidate LSP/query
  caches before the RPC response.

### Phase 6: Move Overlay Shell Execution

- Adapt `OverlayAuditor` so the sidecar executes overlay locally instead of
  uploading `overlay_run.py` from the backend per service instance.
- Keep `overlay_runtime/` under the sidecar state root and version it by runtime
  hash.
- Preserve the current gitinclude/gitignore routing and result metadata.

Verification:

- Shell writes to tracked/unignored files go through strict-base OCC.
- Shell writes to gitignored files direct-merge and show
  `gitignore_direct_merged_paths`.
- Mixed partial apply behavior is unchanged.
- Dot-git writes remain rejected.
- No backend-side overlay bundle upload is needed during normal shell calls.

### Phase 7: Cut Over Context Wiring

- Switch `ensure_code_intelligence_runtime` to attach sidecar RPC clients when
  readiness succeeds.
- Keep backend-hosted `AuditedSandboxApi` and `SvcCodeIntelligence` behind an
  explicit fallback flag for one migration window.
- Add telemetry showing selected mode: `sidecar` or `backend_hosted`.

Verification:

- Tool context contains `sandbox_api=SidecarSandboxApi` and
  `code_intelligence_api=SidecarCodeIntelligenceApi`.
- Agent-visible tools do not call `SandboxTransport` for mutations or CI
  queries.
- Fallback can be disabled in tests to prove sidecar completeness.

### Phase 8: Remove Backend-Hosted Service Path

- Delete fallback wiring after sidecar stability.
- Remove backend-hosted service registry use from production context wiring.
- Keep whitebox tests for engine classes, but production tools only see RPC
  clients.

Verification:

- Import fences prevent tool code from importing `sandbox.code_intelligence`
  internals.
- Production context preparation cannot attach backend-hosted
  `AuditedSandboxApi` accidentally.

## Test Plan

Focused tests:

- Store tests for every persistent store.
- Sidecar daemon lifecycle tests with fake transport.
- `SidecarCodeIntelligenceApi` contract tests mirroring current
  `SvcCodeIntelligence` tests.
- `SidecarSandboxApi` contract tests mirroring current `AuditedSandboxApi`
  tests.
- OCC conflict tests against sidecar-backed mutation methods.
- Overlay parser and shell result tests against sidecar-backed shell.
- Import-fence tests:
  - tools use only `SandboxApi` and `CodeIntelligenceApi`;
  - sidecar RPC handlers may import CI internals;
  - backend RPC clients may not import mutation internals.

Integration tests:

- Create sandbox, start sidecar, index workspace, restart daemon, query symbols.
- Write file through tool, query symbol immediately, verify cache invalidation.
- Run shell that mutates tracked file, verify OCC metadata and index refresh.
- Run shell that mutates gitignored file, verify direct-merge metadata.
- Force daemon restart after mutation, verify stores persist.

Network-layer regression tests:

- Spy on provider transport calls during normal `ci_query_symbol`; no
  `read_bytes_batch` for index data after warm cache.
- Spy on provider transport calls during `write_file`; no
  `apply_diff_batch_checked` from the backend path after sidecar cutover.
- Spy on provider transport calls during `shell`; no backend overlay runtime
  upload during normal command execution after bootstrap.

## Rollback Strategy

Each phase until Phase 8 keeps the backend-hosted implementation behind an
explicit feature flag:

```text
EPHEMERALOS_CI_RUNTIME=backend_hosted | sidecar
```

Rollback switches context wiring back to backend-hosted APIs. The sidecar store
directory remains in the sandbox and can be ignored or deleted by an explicit
maintenance command. Rollback must not copy sidecar stores back to the backend.

## Open Questions

1. Should the first RPC transport be Unix socket plus provider exec tunnel, or
   a forwarded localhost port?
2. Do we need cross-run edit-history retention, or only per-sandbox lifetime?
3. Should `TimeMachine` snapshots persist across daemon restarts by default, or
   should restart clear undo state?
4. What is the cleanup policy for `$HOME/.ephemeralos/code-intelligence` when a
   sandbox hosts many short-lived workspaces?
5. Should sidecar bootstrap fail closed when persistence schema migration fails,
   or fall back to a fresh state directory?

## Acceptance Criteria

- All agent-visible mutations still go through OCC/overlay.
- CI internal state persists inside the sandbox across daemon restarts.
- Index, cache, edit history, and overlay runtime are not transferred out
  through the provider network layer during normal operation.
- Tool contracts remain unchanged.
- The backend owns orchestration and lifecycle wiring, but not CI state.
- Existing OCC and overlay tests pass, plus sidecar-specific persistence and
  transport-regression tests.
