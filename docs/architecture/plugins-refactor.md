# Plugins Refactor Plan

**Status:** Revised draft after the sandbox runtime migration
**Author:** session 2026-05-03, revised 2026-05-09
**Scope:** Introduce a small `backend/src/plugins/` module for agent-callable
plugin tools and sandbox-resident plugin servers. The first target is an LSP
plugin backed by `basedpyright-langserver`.

## 0. Current Baseline

The older version of this plan assumed a host-side code-intelligence service
and a future sandbox daemon migration. The current checkout has already moved
past that shape:

- `backend/src/code_intelligence/`, `backend/src/sandbox/code_intelligence/`,
  and `backend/src/tools/ci_toolkit/` are gone.
- Public sandbox file operations live under `sandbox.api.tool.*`.
- Host-visible sandbox API calls dispatch into the resident in-sandbox daemon
  through `sandbox.host.daemon_client.call_daemon_api(...)`.
- The daemon dispatch table lives in
  `sandbox.runtime.daemon.rpc.dispatcher.OP_TABLE`.
- Daemon handlers live under `sandbox.runtime.daemon.handler.*`.
- The active workspace truth is not the mutable provider filesystem. It is the
  workspace base plus the active layer-stack manifest.

The plugin plan should therefore be additive. It should not recreate the old
query facade, symbol index, or host-side LSP cache.

## 1. Target Model

A plugin is a source of normal agent tools plus an optional sandbox-resident
server module.

```text
Agent
  -> ToolRegistry["lsp.find_definitions"]
  -> plugins/catalog/lsp/tools/find_definitions.py
  -> sandbox.host.daemon_client.call_daemon_api(...)
  -> resident sandbox daemon AF_UNIX socket
  -> plugin.lsp.find_definitions daemon op
  -> basedpyright session inside the sandbox
  -> ToolResult
```

This is MCP-like in the useful sense: tools call a server process with a small
request/response protocol. It is not a separate MCP control plane. Agent
permissioning remains the existing `allowed_tools` list, and provider access
still goes through the sandbox adapter plus resident daemon.

## 2. Plugin Contract

The public contract stays small:

1. Host-side tools are `BaseTool` instances registered into the existing tool
   factory.
2. Sandbox-side setup is optional and idempotent.
3. Sandbox-side server modules are optional and register daemon operations.
4. Tool names are permissioned directly through existing `allowed_tools`.

There is no host-owned plugin lifecycle object, no host-managed LSP handle, no
new agent definition field, and no compatibility query facade.

## 3. Folder Shape

```text
backend/src/
├── plugins/
│   ├── core/
│   │   ├── discovery.py           # discover catalog plugins
│   │   ├── tool_loader.py         # import tools.make_tools()
│   │   ├── bundle.py              # build/upload selected sandbox plugin files
│   │   ├── setup_runner.py        # run selected setup.sh scripts
│   │   └── server_loader.py       # ensure selected daemon peers are loaded
│   └── catalog/
│       └── lsp/
│           ├── plugin.md
│           ├── tools/
│           │   ├── __init__.py
│           │   ├── registry.py
│           │   ├── find_definitions.py
│           │   ├── find_references.py
│           │   ├── hover.py
│           │   ├── diagnostics.py
│           │   └── query_symbols.py
│           └── sandbox/
│               ├── setup.sh
│               ├── server.py      # registers plugin.lsp.* daemon ops
│               └── basedpyright_session.py
└── sandbox/
    └── runtime/daemon/
        ├── handler/plugins.py     # api.plugin.ensure/load/status
        └── plugin_server.py       # sandbox-side registration helper
```

Rules:

- `plugins/catalog/<name>/tools/__init__.py` exposes
  `make_tools() -> list[BaseTool]`.
- `plugins/catalog/<name>/sandbox/setup.sh` is optional and must write a
  readiness marker under `/tmp/eos-sandbox-runtime/plugins/<name>/`.
- `plugins/catalog/<name>/sandbox/server.py` is optional and must expose
  `register_ops(register_op)`.
- Sandbox plugin modules are private implementation. The agent never sees a
  plugin object.
- Plugin server operation names use `plugin.<plugin_name>.<operation>`.

Example server skeleton:

```python
from sandbox.runtime.daemon.plugin_server import SandboxPluginServer

server = SandboxPluginServer("lsp")


@server.op("hover")
async def hover(args: dict[str, object]) -> dict[str, object]:
    ...


def register_ops(register_op) -> None:
    server.register_ops(register_op)
```

## 4. Host Wiring

`tools.factory._register_builtins()` should add one call after built-ins:

```text
plugins.core.tool_loader.register_plugin_tools()
```

That function discovers `plugins/catalog/*/tools/`, imports each
`make_tools()`, and registers the returned `BaseTool` instances just like
built-in tools. `AgentDefinitionValidator` continues to validate against the
actual registered tool names.

The tool implementation stays thin:

```text
lsp.find_definitions tool
  -> require sandbox_id from ToolExecutionContextService
  -> plugins.core.server_loader.ensure_plugin_server("lsp", sandbox_id)
  -> call_daemon_api(sandbox_id, "plugin.lsp.find_definitions", payload)
  -> normalize daemon response into ToolResult
```

Provider-specific imports stay outside plugin tools. Daytona remains behind the
registered sandbox provider adapter.

## 5. Sandbox Setup And Loading

Selected plugin setup should be derived from the final tool surface:

1. Build the agent's final `allowed_tools` and terminal tool list.
2. Map selected namespaced tools back to catalog plugins.
3. Upload the normal sandbox runtime bundle as today.
4. Upload selected plugin `sandbox/` folders to
   `/tmp/eos-sandbox-runtime/plugins/<name>/`.
5. Run each selected `setup.sh` once in deterministic order.
6. Ask the resident daemon to load each selected server module through a new
   `api.plugin.ensure` operation.
7. Fail startup or the first tool call with a clear setup error if the marker
   or server op is missing.

`api.plugin.ensure` should be idempotent. It imports the selected
`server.py`, calls `register_ops(dispatcher.register_op)`, and treats already
registered identical ops as success.

This makes the plugin server easy to author without giving the host a direct
handle to a sandbox process. The existing daemon remains the only cross-sandbox
gateway.

## 6. LSP Plugin

Agent-visible tool names:

- `lsp.find_definitions`
- `lsp.find_references`
- `lsp.hover`
- `lsp.diagnostics`
- `lsp.query_symbols`

The sandbox server owns:

- verifying `basedpyright-langserver` is installed,
- starting and reusing the basedpyright stdio child,
- sending LSP initialize/open/change requests,
- mapping daemon request payloads to LSP JSON-RPC requests,
- returning structured JSON-safe results.

The host does not connect directly to basedpyright over stdio or TCP.
`basedpyright-langserver` is a child of the sandbox-side plugin server path.
The host only calls daemon ops.

## 7. Basedpyright On A Layer-Stack Workspace

The key constraint is that the workspace root is not the source of truth after
the sandbox base is built. The source of truth is:

```text
workspace base layer
  (the captured base repo)
  + active layer-stack manifest
  -> materialized snapshot view
  -> basedpyright rootUri
```

basedpyright can still work because it only requires a real filesystem tree for
the LSP session. The plugin server must provide that tree from the active
manifest instead of pointing basedpyright at the provider workspace directory.

Required flow for each LSP session:

1. Read `WorkspaceBinding` from `layer_stack_root`.
2. Read the active manifest from `LayerStackManager`.
3. Acquire or prepare a workspace snapshot for that manifest.
4. Start basedpyright with `rootUri = file://<snapshot-lowerdir>`.
5. Map user paths through the workspace binding:
   repo-relative or workspace-absolute path -> layer path -> snapshot path.
6. Use `file://<snapshot-lowerdir>/<layer_path>` for document URIs.
7. Reuse the basedpyright session only while the manifest key is unchanged.
8. When the manifest version/root changes, start or switch to a new session.
9. Release snapshot leases when evicting the matching basedpyright session.

The plugin must not run basedpyright against `binding.workspace_root` for
workspace files, because that directory can be stale relative to committed
layer-stack mutations. It may still use the sandbox Python environment for
interpreter and dependency discovery.

Two implementation choices are acceptable:

- Lease-backed session: keep `prepare_workspace_snapshot(...)` leased for the
  lifetime of the basedpyright process, and release it on session eviction.
- Cache-backed session: materialize a plugin-owned snapshot directory keyed by
  manifest version/root hash, then run basedpyright there and garbage-collect
  old cache entries.

Start with the lease-backed session. It aligns with the current layer-stack
lease/GC contract and avoids inventing a second cache invalidation policy.

Plugin mutations remain out of scope for the LSP plugin. If a future plugin op
does mutate workspace files, it must call the existing sandbox write/edit/shell
paths so OCC remains the mutation gate.

## 8. Execution Sequence

1. Add plugin discovery and tool registration.
2. Add selected-plugin setup derivation from the final tool surface.
3. Add selected plugin bundle upload.
4. Add `sandbox.runtime.daemon.handler.plugins` with
   `api.plugin.ensure` and `api.plugin.status`.
5. Add the tiny sandbox-side plugin server helper.
6. Implement `plugins/catalog/lsp/` with setup, host tools, and sandbox server.
7. Run a live LSP spike against a real sandbox:
   - create or use a sandbox with manifest version `>= 1`,
   - install/verify basedpyright,
   - load `plugin.lsp.*` daemon ops,
   - call hover/definition/references/diagnostics/symbol query through tools,
   - edit a file through `write_file` or `edit_file`,
   - verify a later LSP call sees the new manifest, not the stale provider
     workspace.
8. Delete only dead compatibility code found during implementation. Do not
   recreate `code_intelligence` or `ci_toolkit` shims.

## 9. Tests

Focused unit tests:

- plugin discovery ignores plugin folders without `tools.make_tools()`,
- plugin tools register into `tools.factory` and validate via existing tool
  names,
- selected tool names map to unique plugin setup scripts,
- selected plugin bundles include `sandbox/setup.sh` and `sandbox/server.py`,
- `api.plugin.ensure` registers expected daemon ops and is idempotent,
- LSP path mapping uses layer-stack snapshot roots, not `workspace_root`,
- basedpyright session cache invalidates when manifest version/root changes.

Focused live test:

```text
public sandbox API write_file("pkg/mod.py", ...)
  -> lsp.hover/query through agent-visible plugin tool
  -> public sandbox API edit_file("pkg/mod.py", ...)
  -> lsp.hover/query again
  -> assert second answer reflects the committed manifest update
```

## 10. Risks

| Risk | Mitigation |
|------|------------|
| Setup is slow or flaky | Keep setup idempotent, marker-based, and selected-tool driven. |
| Daemon op table becomes a dumping ground | Require plugin op names under `plugin.<name>.*` and route through one plugin handler. |
| basedpyright sees stale files | Key sessions by active manifest version/root and never use `workspace_root` as the LSP root for workspace code. |
| Snapshot leases leak | Tie session eviction to lease release and expose `api.plugin.status` for active sessions. |
| Plugin tools leak Daytona details | Tools only call `call_daemon_api`; provider details remain inside the sandbox adapter. |
| Plugin server bypasses OCC | LSP plugin is read-only. Future mutating plugin ops must call existing sandbox mutation APIs. |

## 11. Out Of Scope

- External plugin distribution.
- Multi-language LSP support.
- A separate MCP server exposed to the host.
- Host-managed plugin lifecycle hooks.
- Per-plugin permissions beyond normal tool names.
- Plugin writes that bypass OCC.
