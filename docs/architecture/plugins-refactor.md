# Plugins Refactor Plan

**Status:** Draft, awaiting execution
**Author:** session 2026-05-03
**Scope:** Adds `backend/src/plugins/`; replaces the in-house code-intelligence query surface with directly agent-callable plugin tools. Deletes the in-house symbol index/LSP host cache/query runtime after the LSP plugin tools are wired.
**Companion doc:** `occ-overlay-daemon-refactor.md` covers the guardrail/daemon relocation. This plan only owns the query-tool replacement.

## 0. Motivation

Today the in-house symbol index, symbol extractor, file discovery, and LSP-host-with-cache reimplement what `basedpyright` already provides as an LSP backend. Two costs:

1. **Duplicated capability.** basedpyright owns a complete language server; the host-side cache and document-version tracking exist only because we wrap it.
2. **Wrong boundary.** Query capability is modeled as a backend service object instead of as tools agents can call directly. That makes sandbox connection and LSP lifecycle look like platform concerns when they are actually implementation details of the query tools.

End state: a plugin is just a source of tools, plus an optional sandbox setup script. Agents call those tools through the same registry and `allowed_tools` mechanism as built-in tools. If a plugin tool needs a process or server inside the sandbox, the tool owns the full path from agent invocation to sandbox connection.

## 1. Design principles

### 1.1 Small plugin contract

A plugin provides only:

1. Agent-callable tools.
2. An optional `setup.sh` if those tools require sandbox-side dependencies.

That is the public contract. No `Plugin` lifecycle object, no host-owned `on_load`, no host-owned `on_teardown`, no framework-managed language-server handle, and no plugin-specific service facade.

Private implementation modules are still fine inside a plugin, but they are free-form helper code, not part of the plugin contract. A plugin can add `client.py` or `session.py` if that shape is useful; those files are not mandatory.

### 1.2 Tools are directly accessible by agents

Plugin tools register into the existing tool registry as normal `BaseTool` instances. Agent definitions continue to expose tools through `allowed_tools` and `terminals`; there is no separate `plugin_toolkits` field.

Example agent-facing names:

```yaml
allowed_tools:
  - lsp.find_definitions
  - lsp.find_references
  - lsp.hover
  - lsp.diagnostics
  - lsp.query_symbols
```

The plugin system is discovery and packaging. Permissioning remains tool-level.

### 1.3 Tool-owned sandbox connection

Each plugin tool encapsulates the sandbox path it needs:

1. Read the current `ToolExecutionContext`.
2. Require or resolve `sandbox_id` and the sandbox API/transport already present in the context.
3. Verify that the plugin setup marker exists, or surface a clear setup error.
4. Ensure any sandbox-local process/server needed for the tool is running.
5. Connect to that sandbox-local process/server.
6. Issue the request and normalize the result into a tool response.

If a plugin server is hosted inside the sandbox, the tool must handle the full process from agent invocation to sandbox connection. The plugin framework does not pre-open the connection on behalf of the tool.

Tools may share a small private connection cache keyed by sandbox id when the protocol benefits from a warm process, but the cache is owned by the plugin tool implementation and must be restartable after failure.

## 2. End-state shape

```text
backend/src/
├── plugins/
│   ├── core/
│   │   ├── registry.py            # discover catalog plugin folders
│   │   ├── tool_loader.py         # load plugin tool factories
│   │   ├── setup_runner.py        # compose/run selected sandbox setup scripts
│   │   └── errors.py
│   └── catalog/
│       └── lsp/
│           ├── setup.sh           # optional idempotent sandbox dependency setup
│           ├── tools/             # required: package mirroring backend/src/tools/*
│           └── plugin.md          # required: human-facing plugin contract and usage notes
└── sandbox/                       # see occ-overlay-daemon-refactor.md
```

`find_definitions`, `find_references`, `hover`, `diagnostics`, and `query_symbols` are no longer methods on `CodeIntelligenceService`. They are normal tools registered from `plugins/catalog/lsp/tools/`.

## 3. Plugin folder contract

There is no `manifest.toml` in v1. The catalog convention is filesystem-first:

```text
plugins/catalog/<plugin_name>/
├── setup.sh                       # optional; only for sandbox dependency setup
├── tools/                         # required; same package pattern as backend/src/tools/*
│   ├── __init__.py                # re-export the package factory
│   ├── registry.py                # collect/register plugin tools
│   ├── <tool_name>.py             # one BaseTool per public tool
│   └── _helpers.py                # optional private helpers, like backend/src/tools/*/_*.py
└── plugin.md                      # required; describes tools, setup, limits, examples
```

Rules:

- The plugin name is the catalog directory name.
- `tools/` is the only required code entrypoint package. It mirrors
  `backend/src/tools/<toolkit>/`: `__init__.py` re-exports a factory,
  `registry.py` collects `BaseTool` objects, and public tools live in
  individual modules.
- The generic plugin-loader contract is `tools.make_tools() -> list[BaseTool]`.
  Internally, `registry.py` may use a more specific helper like
  `make_lsp_tools()`, but `tools/__init__.py` must expose `make_tools()` for
  discovery.
- `setup.sh` is optional and only valid for sandbox plugins.
- `plugin.md` is documentation and catalog metadata for humans. It is not a lifecycle hook and does not declare tool schemas.
- Helper files should follow the existing `backend/src/tools` style: keep them
  private inside the `tools/` package, usually as underscore-prefixed modules.
  `client.py` and `session.py` are acceptable helper names when useful, but
  they are not part of the required structure.
- Setup scripts must be idempotent.
- Setup scripts install prerequisites and write a readiness marker. They do not start long-lived tool servers; tool calls own server startup/connection.
- Tool names are namespaced by plugin, unless a tool has a deliberate built-in replacement name.

## 4. Agent and runtime wiring

### 4.1 Tool registration

The default tool catalog gains plugin tools during startup:

1. `plugins.core.registry` discovers `catalog/*/tools/`.
2. `plugins.core.tool_loader` imports each `tools.make_tools()` factory.
3. Returned `BaseTool` instances register into the existing `ToolRegistry`.
4. `AgentDefinitionValidator` validates plugin tool names the same way it validates built-in tool names.
5. `_build_agent_tool_registry` filters to `allowed_tools ∪ terminals` exactly as it does today.

The agent sees no plugin object. It sees callable tools.

### 4.2 Sandbox setup

Sandbox bootstrap resolves setup from the selected tool surface:

1. Build the final tool surface for the agent.
2. Map selected plugin tools back to their catalog directory.
3. Compose each selected plugin's `setup.sh`, when present, in deterministic order.
4. Run the composed setup before the agent starts.
5. Fail the agent startup if a required setup script exits non-zero.

Setup runs because a selected tool requires it, not because the agent selected an abstract plugin toolkit.

### 4.3 Tool call path

```text
Agent
  -> tool_registry["lsp.find_definitions"]
  -> LspFindDefinitionsTool.execute(context, args)
  -> private LSP helper code
  -> sandbox API/transport from ToolExecutionContext
  -> ensure LSP server/process in sandbox
  -> connect/request/response
  -> ToolResult
```

This keeps the sandbox connection hidden behind the tool. The platform only provides the normal tool context and sandbox API.

### 4.4 Existing live evidence and connection model

There is prior live evidence that a basedpyright child can run inside a Daytona
sandbox and answer a real LSP request. The recorded path was:

```text
orchestrator
  -> Daytona transport exec
  -> in-sandbox Python bridge
  -> in-sandbox Unix-socket CI daemon
  -> basedpyright-langserver --stdio child
  -> textDocument/definition response
```

That proves sandbox-resident basedpyright can launch and answer an LSP query.
It does **not** prove that the host can or should connect directly to
`basedpyright-langserver`. basedpyright is a stdio language-server process; the
connection that crosses the sandbox boundary is the harness-owned transport
call into an in-sandbox bridge/gateway.

The LSP plugin must preserve this distinction:

- The tool owns the agent-facing call.
- The sandbox side owns the basedpyright process and JSON-RPC stdio session.
- The cross-boundary connection goes through the sandbox API/transport, not a
  direct host TCP/stdio handle to basedpyright.
- The first implementation step is a plugin-specific live spike that installs
  the LSP plugin setup, starts the sandbox-side basedpyright path, calls the
  server from outside the sandbox, and records the connection shape plus
  timings.

## 5. LSP plugin

### 5.1 Public files

```text
plugins/catalog/lsp/
├── setup.sh
├── tools/
│   ├── __init__.py
│   ├── registry.py
│   ├── find_definitions.py
│   ├── find_references.py
│   ├── hover.py
│   ├── diagnostics.py
│   └── query_symbols.py
└── plugin.md
```

`tools/__init__.py` exports `make_tools()`, following the same package pattern
as `backend/src/tools/sandbox_toolkit/__init__.py` and
`backend/src/tools/sandbox_toolkit/registry.py`.

The public tool modules are:

- `lsp.find_definitions`
- `lsp.find_references`
- `lsp.hover`
- `lsp.diagnostics`
- `lsp.query_symbols`

### 5.2 Private implementation

No helper file is mandatory. Do not pre-create `lsp_rpc.py`, `process.py`,
`paths.py`, `client.py`, or `session.py` as part of the architecture. Start
with the required `tools/` package and split out helper functions or modules
only when the code actually needs them.

```text
plugins/catalog/lsp/
├── setup.sh
├── tools/
│   ├── __init__.py
│   ├── registry.py
│   ├── <tool modules>
│   └── _helpers.py                # optional
└── plugin.md
```

The first implementation should make full use of `basedpyright-langserver` as
the language-intelligence authority. The plugin may still need tiny plumbing for
process startup, JSON-RPC framing, request correlation, or file-URI conversion,
but that plumbing must stay thin and private. It is not a replacement query
engine, cache, symbol index, or host-side LSP framework.

For the first basedpyright-backed implementation, the `tools/` package owns the
full call path:

- detecting the active sandbox from tool context,
- ensuring `basedpyright-langserver` is installed and runnable in the sandbox,
- starting or reusing the sandbox-local basedpyright server/process,
- initializing the server with the sandbox workspace,
- sending `textDocument/didOpen` / `textDocument/didChange` so basedpyright's
  in-memory model is authoritative,
- using basedpyright LSP requests for the tool behavior:
  `textDocument/definition`, `textDocument/references`, `textDocument/hover`,
  diagnostics, and `workspace/symbol` or `textDocument/documentSymbol` for
  symbol lookup,
- reconnecting once after failure, then surfacing a clear tool error,
- closing stale handles when the sandbox disappears.

No host-side symbol index, no document-version cache, no local syntax-check
fallback that pretends to be diagnostics, and no `CodeIntelligenceService` query
facade remain. If basedpyright cannot answer a query, the tool should return an
explicit unavailable/unsupported result instead of falling back to the deleted
in-house query surface.

## 6. Deletions

### 6.1 Code

- `sandbox/code_intelligence/indexing/`
- `sandbox/code_intelligence/language_server/cache.py`
- `sandbox/code_intelligence/language_server/client.py`
- `sandbox/code_intelligence/language_server/daemon_queries.py`
- `sandbox/code_intelligence/language_server/jsonrpc.py`
- `sandbox/code_intelligence/language_server/lsp_child.py`
- `sandbox/code_intelligence/language_server/lsp_host.py`
- `sandbox/code_intelligence/language_server/models.py`
- `sandbox/code_intelligence/language_server/path_helpers.py`
- `sandbox/code_intelligence/language_server/transport.py`
- `sandbox/code_intelligence/language_server/utils.py`
- `sandbox/code_intelligence/daemon/index_store.py`

Any unavoidable JSON-RPC/process/path plumbing needed by the LSP implementation
moves into the plugin as private implementation. It should not preserve the old
module split unless the code size justifies it.

### 6.2 Tools

- `tools/ci_toolkit/_query_runtime.py`
- Any `tools/ci_toolkit/` wrappers that only forward into deleted query service methods

The replacement query tools live under `plugins/catalog/lsp/tools/` and are registered directly.

### 6.3 Tests

- Tests targeting symbol-index extraction/storage.
- Tests targeting the deleted language-server host/cache machinery.
- Tests targeting deleted `CodeIntelligenceService` query methods.

Replacement tests should cover plugin discovery, setup selection, direct tool registration, and LSP tool execution against a real Python file.

### 6.4 No backward-compat shims

Every external call site is rewritten in the same change set. No re-export shims, no deprecated query-service wrappers, and no temporary `plugin_toolkits` compatibility field.

## 7. Sequenced execution

Plugin work lands before the OCC/Overlay/daemon relocation so query call sites can switch to direct tools in one pass.

```text
0. Pre-step: prove sandbox-hosted basedpyright connectivity
   - Create a throwaway live sandbox with a real Python checkout/file
   - Run the candidate LSP plugin setup path, or an equivalent setup script,
     to install basedpyright and verify `command -v basedpyright-langserver`
   - Start `basedpyright-langserver --stdio` inside the sandbox through the
     intended sandbox-side bridge/gateway shape
   - Connect from outside the sandbox only through the sandbox API/transport
     path; do not assume direct host stdio/TCP connectivity to basedpyright
   - Send initialize/initialized, didOpen, then at least
     textDocument/definition, textDocument/references, textDocument/hover,
     diagnostics, and one symbol query request
   - Record the exact process topology, bridge code shape, failure behavior,
     and cold/warm timings in a timing artifact or architecture note
   - Gate step 1 on this proof; if the bridge shape is awkward, update this
     plan before building the plugin catalog

1. Create the minimal plugin catalog infrastructure
   - core/{registry.py, tool_loader.py, setup_runner.py, errors.py}
   - no Plugin protocol, no lifecycle hooks
   - tests for folder discovery, tool factory loading, setup selection

2. Register plugin tools in the existing tool surface
   - tools/core/factory.py discovers plugin tool factories
   - tool validation accepts plugin tool names through the existing registry
   - agent definitions keep using allowed_tools

3. Wire selected-tool setup into sandbox bootstrap
   - selected plugin tools -> unique setup.sh scripts
   - setup scripts run idempotently before agent start
   - non-zero setup exits fail startup with a clear error

4. Author plugins/catalog/lsp/
   - setup.sh
   - tools/ package with __init__.py, registry.py, and the 5 namespaced tool modules
   - plugin.md documenting tool behavior, setup, and limits
   - helper functions/modules only where the tool implementation actually needs them

5. Rewrite query call sites
   - remove service.find_definitions/find_references/hover/diagnostics/query_symbols usage
   - agent-facing query capability comes only from LSP plugin tools

6. Delete the in-house query surface
   - indexing/
   - language_server/ host/cache/query modules
   - daemon/index_store.py
   - tools/ci_toolkit/_query_runtime.py and forwarding wrappers

7. Smoke test
   - run an agent with the 5 LSP tool names in allowed_tools
   - verify setup runs
   - verify each tool returns sensible output against a real Python file

8. Hand off to occ-overlay-daemon-refactor.md
   - lifecycle/workspace.py swaps remaining CI-service references to OCC/Overlay plus plugin tool lookup
   - code_intelligence/ can be emptied and removed after both plans land
```

## 8. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| LSP cold start latency | Tool-owned helper code lazily starts and reuses the server/process per sandbox. |
| Setup script not run before tool call | Derive setup from the final selected tool surface during sandbox bootstrap; fail startup on setup failure. |
| Sandbox-local server crashes mid-session | Tool-owned helper code reconnects once, then returns a clear tool error. |
| Tool implementation leaks provider details | Tools consume the existing sandbox API/transport from `ToolExecutionContext`; provider-specific imports stay outside plugin tools. |
| External callers still use query service methods | Enumerate and rewrite all query call sites before deleting the old service surface. |

## 9. Out of scope

- Multi-language LSP backend support beyond the first basedpyright-backed Python implementation.
- Plugin dependency graph.
- Plugin versioning/pinning beyond what `setup.sh` enforces.
- External plugin distribution through Python entry points.
- Host-managed plugin lifecycle hooks.
- A separate MCP server/gateway owned by the plugin framework.
- Per-plugin permissions beyond the existing tool-level `allowed_tools` surface.
- Setup-script layered caching beyond idempotent scripts and readiness markers.

## 10. Open questions deferred to execution

- Whether old bare query-tool names should become aliases for the new `lsp.*` names.
- Whether the LSP process/connection cache is process-local, run-local, or stored behind a sandbox lifecycle registry.
- Exact readiness marker path for sandbox setup scripts.

These do not change the plan shape; resolve in the relevant step.
