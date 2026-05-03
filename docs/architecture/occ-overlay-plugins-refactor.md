# OCC + Overlay + Plugins Refactor Plan

**Status:** Draft, awaiting execution
**Author:** session 2026-05-03
**Scope:** ~10K LoC, ~50 files across `backend/src/sandbox/code_intelligence/`, `backend/src/sandbox/api/`, `backend/src/tools/ci_toolkit/`, `backend/src/agents/` (agent definition), and tests under `backend/tests/test_sandbox/`.

## 0. Motivation

Today `sandbox/code_intelligence/` is an over-broad umbrella that bundles three unrelated concerns:

1. **Write/edit guardrail** (OCC arbiter, content manager, write coordinator, edit history ledger).
2. **Command-execution guardrail** (overlay, command committer, process exec).
3. **Code intelligence queries** (in-house symbol index, symbol extractor, file discovery, LSP host with cache, daemon-side index store).

The query surface duplicates capability that `basedpyright` already provides for free. The umbrella name (`code_intelligence`) is misleading once queries leave. The two guardrails serve different chokepoints (file edits vs sandbox cmd execution) and deserve to be peers, not siblings under a misleading parent.

## 1. End-state architecture

Three independent top-level concerns:

```
backend/src/
├── plugins/                       # NEW: generic plugin host, NOT sandbox-coupled
│   ├── core/                      # plugin protocol, manifest, registry, setup composer
│   └── catalog/
│       └── basedpyright/          # first plugin: LSP for Python via basedpyright
└── sandbox/
    ├── occ/                       # was code_intelligence/mutations + ledger
    ├── overlay/                   # was code_intelligence/overlay
    └── daemon/                    # was code_intelligence/daemon, scope-narrowed
```

`sandbox/code_intelligence/` ceases to exist. The two guardrails (`occ`, `overlay`) are siblings under `sandbox/`. Plugins are decoupled and live at the repo root under `backend/src/plugins/`.

### 1.1 Naming decisions

- **No `guardrail/` umbrella.** OCC and Overlay are two separate modules.
- **Plugins are not code-intelligence-specific.** Future plugins (linters, formatters, search engines) can plug in without touching `sandbox/`.

### 1.2 OCC chokepoint

Every file edit converges on a single OCC class. `mutation_service.py`, `arbiter.py`, `content_manager.py`, `patcher.py`, `time_machine.py`, and `write_coordinator/` collapse into OCC internals. External callers see one entry point.

### 1.3 Overlay chokepoint

Every `service.cmd()` routes through `sandbox/overlay/`. Existing overlay logic relocates with minimal change — the chokepoint is already in place; the move just makes it visible.

### 1.4 Code intelligence queries → plugin tools

`find_definitions`, `find_references`, `hover`, `diagnostics`, `query_symbols` are no longer methods on a service object. They are tools registered by the `basedpyright` plugin and exposed to agents through the plugin host's tool surface (MCP-style).

## 2. Plugin host

### 2.1 Layout

```
backend/src/plugins/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── manifest.py                # parse manifest.toml → PluginManifest dataclass
│   ├── protocol.py                # Plugin Protocol: on_load(sandbox), on_teardown(), tools()
│   ├── registry.py                # scan catalog/, build name→manifest map, load entrypoints
│   ├── tool.py                    # ToolSpec(name, handler, schema, description)
│   ├── setup_builder.py           # compose ordered, idempotent setup script from N plugins
│   └── errors.py                  # PluginError, ManifestError, SetupError
└── catalog/
    ├── __init__.py
    └── basedpyright/
        ├── __init__.py
        ├── manifest.toml          # plugin metadata only — tools come from code
        ├── setup.sh               # installs basedpyright in sandbox
        ├── plugin.py              # BasedpyrightPlugin: on_load spawns langserver, tools() returns ToolSpecs
        └── lsp_rpc.py             # minimal jsonrpc stdio client (~80 LoC: framing + req/resp correlation)
```

Tests live under `backend/tests/test_plugins/`.

### 2.2 Manifest format

```toml
name = "basedpyright"
description = "basedpyright LSP for Python"
languages = ["python"]
setup = "setup.sh"
entrypoint = "plugin:BasedpyrightPlugin"
```

Tools are NOT declared in the manifest. The plugin's `tools()` method is the source of truth.

### 2.3 Plugin protocol

```python
class Plugin(Protocol):
    name: str

    def on_load(self, sandbox: Sandbox) -> None: ...
    def on_teardown(self) -> None: ...
    def tools(self) -> list[ToolSpec]: ...
```

`on_load` is called once per sandbox bind. For `basedpyright`, this spawns `basedpyright-langserver --stdio` inside the sandbox via `sandbox.cmd` and holds the stdio channel open through `lsp_rpc.py`. No host-side cache, no document version tracking — basedpyright owns its own state inside the sandbox.

### 2.4 Setup script composition

`plugins.core.setup_builder.build(plugin_names: list[str]) → str` returns one ordered, idempotent shell script that bootstraps every requested plugin's setup fragment. Each plugin's `setup.sh` MUST be idempotent (safe to re-run). The builder handles dedup; ordering follows manifest declaration order (no dep graph in v1).

### 2.5 Agent definition wiring

```python
# agent_definition (existing schema)
plugin_toolkits: list[str]   # NEW field — names of required plugins
```

At sandbox bootstrap:

1. Resolve `agent.plugin_toolkits` → manifests via `plugins.core.registry`.
2. `setup_builder.build(...)` produces the composed setup script.
3. Sandbox bootstrap runs the script (once, before agent starts).
4. Each plugin's `on_load(sandbox)` is invoked; `tools()` results are registered on the agent's tool surface.
5. Agent invokes plugin tools through the same surface as built-in tools.

### 2.6 basedpyright lifecycle

- **Long-lived inside the sandbox.** One langserver per sandbox, spawned at `on_load`, killed at `on_teardown`.
- Host side keeps only the stdio handle and the jsonrpc correlator.
- `cache.py`, `lsp_host.py`, document-version tracking — DELETED.

## 3. Sandbox-side modules

### 3.1 `sandbox/occ/`

Single OCC class is the chokepoint. Internals:

```
sandbox/occ/
├── __init__.py
├── occ.py                         # OCC class (the chokepoint) — wraps everything below
├── arbiter.py                     # was mutations/arbiter.py
├── content_manager.py             # was mutations/content_manager.py
├── patcher.py                     # was mutations/patcher.py
├── time_machine.py                # was mutations/time_machine.py
├── write_coordinator/             # unchanged structure, relocated
├── ledger_store.py                # was daemon/ledger_store.py — edit history
├── types.py                       # was core/types.py (EditSpec, WriteSpec, MoveSpec, OperationResult)
├── hashing.py                     # was core/hashing.py
├── registry.py                    # get_occ(sandbox_id) → OCC
├── telemetry.py                   # OCC-specific portion of code_intelligence/telemetry.py
└── backends/
    ├── __init__.py
    ├── protocol.py
    ├── in_process.py              # OCC running in current process
    └── daemon.py                  # OCC routed through sandbox daemon RPC
```

External API: every file edit goes through `OCC.apply_edit(...)`, `OCC.write_file(...)`, `OCC.delete_file(...)`, `OCC.move_file(...)`, `OCC.commit_*(...)`, `OCC.undo_last_edit(...)`. No other surface accepts edit specs.

### 3.2 `sandbox/overlay/`

Mostly relocation:

```
sandbox/overlay/
├── __init__.py
├── overlay.py                     # Overlay class (chokepoint) — every cmd goes here
├── auditor.py                     # was overlay/auditor.py
├── command_committer.py
├── command_executor.py
├── config.py
├── daemon_local.py
├── process_exec.py
├── results.py
├── run.py
├── support.py
├── types.py
├── runtime/                       # unchanged
├── registry.py                    # get_overlay(sandbox_id) → Overlay
├── telemetry.py                   # overlay-specific portion of code_intelligence/telemetry.py
└── backends/
    ├── protocol.py
    ├── in_process.py
    └── daemon.py
```

External API: every sandbox cmd goes through `Overlay.cmd(sandbox, command, **kwargs)`. No other surface invokes the sandbox shell.

### 3.3 `sandbox/daemon/`

The daemon process moves out from under `code_intelligence/` and becomes a sandbox-level concern. Single daemon process per sandbox; `handlers.py` splits into:

```
sandbox/daemon/
├── __init__.py
├── __main__.py
├── server.py
├── client.py
├── launcher.py
├── guard.py
├── state.py
├── storage.py                     # daemon-process storage primitives (NOT the symbol index)
├── paths.py
├── protocol.py
├── wire.py                        # symbol-query wire types DELETED
├── handlers/
│   ├── __init__.py
│   ├── edit.py                    # was handlers.py edit-related portion → calls into occ.*
│   └── cmd.py                     # was handlers.py cmd-related portion → calls into overlay.*
```

DELETED from daemon: `index_store.py`, all symbol-query RPC handlers, all symbol-related wire types.

### 3.4 Shared path utilities

`code_intelligence/core/path_utils.py` and `core/constants.py` → `sandbox/_paths.py` (single util module shared by occ + overlay + daemon). Anything occ-specific lives in `occ/`; anything overlay-specific in `overlay/`.

## 4. Deletions

### 4.1 Code

- `sandbox/code_intelligence/indexing/` (entire directory: `symbol_index.py`, `symbol_extractor.py`, `file_discovery.py`)
- `sandbox/code_intelligence/language_server/` (entire directory: `cache.py`, `client.py`, `daemon_queries.py`, `jsonrpc.py`, `lsp_child.py`, `lsp_host.py`, `models.py`, `path_helpers.py`, `telemetry.py`, `transport.py`, `utils.py`)
- `sandbox/code_intelligence/daemon/index_store.py`
- `sandbox/code_intelligence/service.py` (CodeIntelligenceService facade — replaced by `OCC` + `Overlay` separately)
- `sandbox/code_intelligence/registry.py` (replaced by `occ/registry.py` + `overlay/registry.py`)
- `sandbox/code_intelligence/__init__.py`, `telemetry.py`, `backends/` — all relocated or deleted
- `sandbox/code_intelligence/` (the directory itself, after everything inside has moved or been deleted)

### 4.2 API surface

- `sandbox/api/code_intelligence_api.py` (entire file)
- `sandbox/api/code_intelligence_impl.py` (entire file)
- Query-related types in `sandbox/api/models.py` (`SymbolInfo`, `ReferenceInfo`, `HoverResult`, `Diagnostic`, etc. — relocated only if still referenced; otherwise deleted)
- New: `sandbox/api/occ_api.py` and `sandbox/api/overlay_api.py` if external HTTP/RPC surface is needed

### 4.3 Tools

- `tools/ci_toolkit/_query_runtime.py` (entire file)
- Anything in `tools/ci_toolkit/` that calls deleted query methods — delete or rewrite to plugin tools

### 4.4 Tests

- `backend/tests/test_sandbox/test_code_intelligence/test_*indexing*.py`
- Tests targeting the deleted symbol query handlers
- Tests for the deleted `language_server/` host/cache machinery

### 4.5 No backward-compat shims

Per agreed scope: every external call site is rewritten in the same change set. No re-export shims, no deprecation wrappers.

## 5. External call sites to rewrite

Found via grep:

- `sandbox/lifecycle/workspace.py` — uses `service.symbol_index`, `service.lsp_client`, etc.
- `sandbox/api/code_intelligence_api.py` — DELETE
- `sandbox/api/code_intelligence_impl.py` — DELETE
- `sandbox/api/models.py` — strip query types
- `sandbox/api/audit.py` — references mutations module
- `tools/ci_toolkit/_query_runtime.py` — DELETE
- `backend/tests/test_sandbox/test_code_intelligence/*` — relocate or delete
- `backend/tests/test_sandbox/test_daemon_*.py` — update for new handler split

## 6. Sequenced execution

Build new → relocate old → delete dead → green tests → docs.

```
1. Create backend/src/plugins/{core,catalog/basedpyright}/ skeleton
   - core/{manifest.py, protocol.py, registry.py, tool.py, setup_builder.py, errors.py}
   - Plugin protocol + ToolSpec dataclass + manifest schema
   - Unit tests for manifest parsing, registry discovery, setup composition

2. Implement plugins.core.registry + setup_builder
   - Discover catalog/* directories with manifest.toml
   - Validate entrypoints loadable
   - setup_builder produces idempotent ordered shell script

3. Author plugins/catalog/basedpyright/
   - manifest.toml (5-line minimal)
   - setup.sh (idempotent install of basedpyright in sandbox)
   - lsp_rpc.py (~80 LoC stdio jsonrpc correlator)
   - plugin.py (BasedpyrightPlugin with on_load, on_teardown, tools() returning 5 ToolSpecs)

4. Add `plugin_toolkits: list[str]` to agent_definition schema
   - Wire sandbox bootstrap: resolve manifests → setup_builder.build → run script
   - Invoke plugin.on_load(sandbox), register tools() on agent surface

5. Smoke test: run a sandbox with agent.plugin_toolkits = ["basedpyright"]; verify each of the 5 tools returns sensible output against a real Python file.

6. Move sandbox/code_intelligence/mutations/ → sandbox/occ/
   - Collapse arbiter + patcher + content_manager + mutation_service into OCC class
   - Keep write_coordinator/, time_machine.py, edit_history_ledger.py as internals

7. Move sandbox/code_intelligence/overlay/ → sandbox/overlay/
   - Verify Overlay.cmd is the only entry; no other module invokes shell

8. Move sandbox/code_intelligence/core/ types
   - EditSpec/WriteSpec/MoveSpec/OperationResult → sandbox/occ/types.py
   - Path normalizers → sandbox/_paths.py
   - hashing → sandbox/occ/hashing.py

9. Move sandbox/code_intelligence/daemon/ → sandbox/daemon/
   - Split handlers.py into handlers/edit.py + handlers/cmd.py
   - DELETE: index_store.py, symbol-query handlers, symbol wire types
   - ledger_store.py moves to sandbox/occ/ledger_store.py

10. Move backends
    - occ-related backend logic → sandbox/occ/backends/{protocol.py, in_process.py, daemon.py}
    - overlay-related backend logic → sandbox/overlay/backends/...
    - DELETE old code_intelligence/backends/

11. New registries
    - sandbox/occ/registry.py: get_occ(sandbox_id), get_occ_if_exists(...), dispose_occ(...)
    - sandbox/overlay/registry.py: get_overlay(sandbox_id), get_overlay_if_exists(...), dispose_overlay(...)
    - Old code_intelligence/registry.py and service.py DELETED

12. Mass deletions
    - code_intelligence/indexing/ (entire dir)
    - code_intelligence/language_server/ (entire dir)
    - api/code_intelligence_api.py + code_intelligence_impl.py
    - tools/ci_toolkit/_query_runtime.py
    - Query types from api/models.py
    - Tests targeting deleted surface

13. Rewrite call sites — no shims
    - sandbox/lifecycle/workspace.py: replace service.symbol_index/lsp_client refs with OCC + plugin lookup
    - api/audit.py: route through OCC
    - Any remaining tools/* references: rewrite or delete

14. Clean up tests
    - Relocate OCC tests to backend/tests/test_sandbox/test_occ/
    - Relocate overlay tests to backend/tests/test_sandbox/test_overlay/
    - Delete indexing/query tests
    - Add backend/tests/test_plugins/ for plugin host

15. make test + ruff check; iterate to green

16. Documentation
    - docs/architecture/code-intelligence-in-sandbox-daemon/ → docs/architecture/occ-overlay/
    - Rewrite phase-08 implementation report to reflect new architecture
    - Add docs/architecture/plugins.md describing the plugin host

17. Final verification: code_intelligence/ directory empty → `git rm -r` it.
```

## 7. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| basedpyright cold start latency | Long-lived langserver per sandbox; on_load is paid once. |
| Sandbox without basedpyright installed | Setup script is mandatory at bootstrap; agent fails to start if any plugin's setup.sh exits non-zero. |
| Daemon handler split breaks RPC compatibility | Same wire protocol, only delivery routing changes. Update tests for new dispatch surface. |
| External callers depending on `service.find_definitions` etc. | All call sites enumerated in §5; rewritten in same change set. |
| Lost edit history during move | `ledger_store.py` relocates as-is; no schema change. |
| Tests for indexing fail to delete cleanly | Each test file inspected; deletion is line-item, not bulk. |

## 8. Out of scope

- Multi-language plugin support beyond basedpyright (deferred — plugin host is ready, no second plugin authored here).
- Plugin dependency graph (v1 ships linear ordered list).
- Plugin versioning / pinning beyond what `setup.sh` enforces.
- MCP-protocol-compatible tool serving over a real socket (the "MCP-shaped" wording is about *plugin nature*, not wire compatibility).

## 9. Open questions deferred to execution

- Exact location of `sandbox/_paths.py` (root of `sandbox/` vs a tiny `sandbox/util/` package).
- Whether `OCC` and `Overlay` should share a base `Chokepoint` interface or remain duck-typed peers.
- Whether the daemon's `handlers/` package is a hard split or just two files in `handlers/`.

These do not change the plan shape; resolve in the relevant step.
