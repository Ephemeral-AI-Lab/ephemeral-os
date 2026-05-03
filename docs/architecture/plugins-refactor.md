# Plugins Refactor Plan

**Status:** Draft, awaiting execution
**Author:** session 2026-05-03
**Scope:** ~3K LoC, ~15 files. Adds `backend/src/plugins/`; deletes the in-house code-intelligence query surface (`indexing/`, `language_server/`, `daemon/index_store.py`) and `tools/ci_toolkit/_query_runtime.py`. Tests under `backend/tests/test_plugins/`.
**Companion doc:** `occ-overlay-daemon-refactor.md` covers the guardrail/daemon relocation. The plugin half lands first so `lifecycle/workspace.py` can swap to the new lookup in a single pass.

## 0. Motivation

Today the in-house symbol index, symbol extractor, file discovery, and LSP-host-with-cache reimplement what `basedpyright` already provides. Two costs:

1. **Duplicated capability.** basedpyright owns a complete language server; the host-side cache and document-version tracking exist only because we wrap it.
2. **Locked-in shape.** Everything lives under `code_intelligence/` and is sandbox-coupled. Future linters, formatters, search engines, or third-party tools have no clean place to plug in.

End state: a generic plugin host at `backend/src/plugins/`, with `basedpyright` as the first plugin. Plugins are not code-intelligence-specific; they're the standard way agents acquire toolkits inside a sandbox.

## 1. End-state shape

```
backend/src/
├── plugins/                       # NEW: generic plugin host, NOT sandbox-coupled
│   ├── core/                      # plugin protocol, manifest, registry, setup composer
│   └── catalog/
│       └── basedpyright/          # first plugin: LSP for Python via basedpyright
└── sandbox/                       # see occ-overlay-daemon-refactor.md
```

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
- `cache.py`, `lsp_host.py`, document-version tracking — DELETED (see §3).

## 3. Deletions

### 3.1 Code

- `sandbox/code_intelligence/indexing/` (entire directory: `symbol_index.py`, `symbol_extractor.py`, `file_discovery.py`)
- `sandbox/code_intelligence/language_server/` (entire directory: `cache.py`, `client.py`, `daemon_queries.py`, `jsonrpc.py`, `lsp_child.py`, `lsp_host.py`, `models.py`, `path_helpers.py`, `telemetry.py`, `transport.py`, `utils.py`)
- `sandbox/code_intelligence/daemon/index_store.py`

### 3.2 Tools

- `tools/ci_toolkit/_query_runtime.py` (entire file)
- Anything else in `tools/ci_toolkit/` that calls deleted query methods — delete or rewrite as plugin tools

### 3.3 Tests

- `backend/tests/test_sandbox/test_code_intelligence/test_*indexing*.py`
- Tests targeting the deleted symbol query handlers
- Tests for the deleted `language_server/` host/cache machinery

### 3.4 No backward-compat shims

Per agreed scope: every external call site is rewritten in the same change set. No re-export shims, no deprecation wrappers.

## 4. Sequenced execution

Plugin half lands first; OCC/Overlay/daemon relocation in the companion doc starts after step 5 here passes smoke test.

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

6. Hand off to occ-overlay-daemon-refactor.md, which will:
   - Rewrite sandbox/lifecycle/workspace.py to use plugin lookup instead of service.symbol_index/lsp_client
   - Delete the in-house query surface listed in §3 above as part of its mass-deletion step

7. After OCC/Overlay/daemon refactor merges:
   - Add docs/architecture/plugins.md describing the plugin host
   - Verify backend/tests/test_plugins/ is green and basedpyright tools are exercised end-to-end
```

## 5. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| basedpyright cold start latency | Long-lived langserver per sandbox; `on_load` is paid once. |
| Sandbox without basedpyright installed | Setup script is mandatory at bootstrap; agent fails to start if any plugin's `setup.sh` exits non-zero. |
| External callers depending on `service.find_definitions` etc. | All call sites enumerated in companion doc §4; rewritten in same change set. |
| Plugin process crash mid-session | Out of scope for v1 — surfaced as tool error; supervisor/restart deferred. |
| Tests for indexing fail to delete cleanly | Each test file inspected; deletion is line-item, not bulk. |

## 6. Out of scope

- Multi-language plugin support beyond basedpyright (deferred — plugin host is ready, no second plugin authored here).
- Plugin dependency graph (v1 ships linear ordered list).
- Plugin versioning / pinning beyond what `setup.sh` enforces.
- MCP-protocol-compatible tool serving over a real socket (the "MCP-shaped" wording is about *plugin nature*, not wire compatibility).
- Per-plugin config on agent definitions (today: bare `list[str]`; richer `PluginRef = {name, config}` deferred).
- Out-of-tree plugin discovery via Python entry points (deferred; v1 is `catalog/` only).
- Plugin capability/permission declaration (today: plugin sees full `Sandbox`).
- Setup-script layered caching (today: re-run on every cold start).
- Plugin test harness (`PluginTestHarness`) — authors hand-roll fixtures for now.

## 7. Open questions deferred to execution

- Manifest parser: hand-rolled vs pydantic v2 vs msgspec. Default to pydantic v2 for stack consistency with FastAPI; revisit if it pulls heavy deps.
- Whether `on_load` should be `async def` from day one (FastAPI stack favors async; spawning a langserver is I/O-bound).
- Where the plugin tool surface plugs into the existing agent tool registry — in-process call vs MCP-style RPC channel.

These do not change the plan shape; resolve in the relevant step.
