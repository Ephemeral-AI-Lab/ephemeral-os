# Plugins Refactor Plan

**Status:** Revised draft — author-surface focused
**Author:** session 2026-05-03, revised 2026-05-09 (round 2)
**Scope:** Add a `backend/src/plugins/` catalog whose author surface is three
files (`plugin.md`, `setup.sh`, `tools/*.py`) and a `backend/src/sandbox/plugin/`
adapter that turns those files into normal agent tools backed by the resident
in-sandbox daemon. The first concrete plugin is LSP, backed by
`basedpyright-langserver`.

## 0. Current Baseline

The orchestrator already moved past the older code-intelligence design:

- `backend/src/code_intelligence/`, `backend/src/sandbox/code_intelligence/`,
  and `backend/src/tools/ci_toolkit/` are gone.
- Public sandbox file operations live under `sandbox.api.tool.*`.
- Host calls into the resident sandbox daemon through
  `sandbox.host.daemon_client.call_daemon_api(...)`.
- The daemon dispatch table lives in
  `sandbox.runtime.daemon.rpc.dispatcher.OP_TABLE` and `register_op(...)`.
- Daemon handlers live under `sandbox.runtime.daemon.handler.*`.
- Tool registration happens in `tools.factory._register_builtins()` via
  `register_tool_instance(tool)` for each `BaseTool`.
- The active workspace truth is the workspace base layer plus the active
  layer-stack manifest; the mutable provider filesystem is not authoritative.

Plugins are additive on top of this baseline. They do not recreate the old
query facade, symbol index, or host-side LSP cache.

## 1. Author Contract

A plugin is a directory with three required files and one optional one:

```text
backend/src/plugins/catalog/<name>/
├── plugin.md           # required — declarative manifest
├── setup.sh            # required — idempotent in-sandbox install script
├── tools/              # required — one Python file per agent-visible tool
│   └── <tool>.py
└── runtime/            # optional — in-sandbox Python ops for stateful plugins
    └── server.py
```

That is the entire surface a plugin author touches. There is no `registry.py`,
no `PluginSpec`, and no host-managed lifecycle object. The framework discovers
plugins by walking `plugins/catalog/*/plugin.md`.

### 1.1 `plugin.md` schema

`plugin.md` is markdown with a YAML frontmatter block. The frontmatter is the
machine-parsable contract; the markdown body is human-facing documentation.

```markdown
---
name: lsp
description: basedpyright-backed LSP tools for Python.
tools:
  - name: lsp.hover
    module: tools/hover.py
  - name: lsp.find_definitions
    module: tools/find_definitions.py
  - name: lsp.find_references
    module: tools/find_references.py
  - name: lsp.diagnostics
    module: tools/diagnostics.py
  - name: lsp.query_symbols
    module: tools/query_symbols.py
setup: setup.sh           # optional; defaults to "setup.sh" if file exists
runtime: runtime/server.py # optional; required when any tool routes to plugin ops
---

# LSP Plugin

Human-facing prose about what this plugin does, how to enable it, and any
prerequisites that the agent or operator should know.
```

Schema rules (enforced by the loader; violations fail discovery loudly):

1. `name` is a non-empty lowercase token, unique across the catalog, and must
   match the directory name.
2. `tools` is an explicit list. Implicit walks of `tools/` are not supported —
   the manifest is the single source of truth for what is exported, and that
   externalization is the whole point of having a manifest.
3. Every `tools[].name` MUST start with `<name>.` (e.g. plugin `lsp` may only
   declare `lsp.*` tools). This makes "which plugin owns this tool" a string
   split, no separate index.
4. Every `tools[].module` is a path relative to the plugin directory and must
   resolve to a Python module that defines exactly one `BaseTool` whose `.name`
   equals `tools[].name`.
5. `setup` is the path to a shell script relative to the plugin directory. If
   omitted and `setup.sh` exists, it is used. If neither, the plugin is treated
   as install-free.
6. `runtime` is the path to a Python module relative to the plugin directory.
   Required iff any of the plugin's tools call `call_plugin(...)` against a
   `plugin.<name>.<op>` operation. The loader does not inspect tool bodies; if
   a tool tries to call a missing op, the call fails at runtime with a clear
   error.

### 1.2 Tool file shape

Each file in `tools/` defines one `BaseTool` using the existing `@tool`
decorator. It receives the sandbox tool execution context, validates input
through its Pydantic model, and either calls existing public sandbox APIs
(stateless plugins) or delegates to the plugin's own in-sandbox runtime via
`call_plugin(...)` (stateful plugins).

Example: `plugins/catalog/lsp/tools/hover.py`

```python
from pydantic import BaseModel

from sandbox.plugin import call_plugin
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.sandbox_toolkit.session import resolve_sandbox_path, sandbox_id_or_error


class HoverInput(BaseModel):
    file_path: str
    line: int
    character: int


@tool(
    name="lsp.hover",
    description="Return basedpyright hover information for a Python symbol.",
    short_description="LSP hover.",
    input_model=HoverInput,
)
async def hover(
    file_path: str,
    line: int,
    character: int,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    sandbox_id, error = sandbox_id_or_error(context)
    if error is not None:
        return error
    payload = {
        "file_path": resolve_sandbox_path(file_path, context),
        "line": line,
        "character": character,
    }
    return await call_plugin(context, plugin="lsp", op="hover", payload=payload)
```

The author imports nothing from the plugin framework except `call_plugin`. There
is no `PluginToolSpec`, no `TOOL_SPEC`/`HANDLER` split, no
`PluginRuntimeContext`. If this tool grows past ~20 lines without the author
adding LSP-specific logic, the contract is leaking and should be fixed.

### 1.3 In-sandbox runtime shape (optional)

When a plugin needs stateful in-sandbox behavior (LSP keeps a long-lived
`basedpyright` child), it ships a `runtime/server.py` that registers
`plugin.<name>.<op>` operations through one decorator:

```python
# plugins/catalog/lsp/runtime/server.py

from sandbox.plugin.runtime import register_plugin_op
from plugins.catalog.lsp.runtime.session_manager import get_session


@register_plugin_op("lsp", "hover")
async def hover(args: dict, ctx) -> dict:
    session = await get_session(ctx)
    return await session.hover(args)


@register_plugin_op("lsp", "find_definitions")
async def find_definitions(args: dict, ctx) -> dict:
    session = await get_session(ctx)
    return await session.find_definitions(args)
```

`ctx` is a small `PluginOpContext` exposing exactly what a runtime op needs:
`workspace_projection()` (lease-backed snapshot view), `layer_stack_root`,
`caller`, and a logger. Concrete plugin runtime modules (e.g.
`basedpyright_session.py`, `paths.py`, `lsp_jsonrpc.py`) live alongside
`server.py` under the plugin's `runtime/` and import from each other freely.
They MUST NOT import `sandbox.*` directly; all sandbox interaction goes through
`PluginOpContext` and `register_plugin_op`.

Stateless plugins skip `runtime/` entirely.

## 2. Folder Shape

```text
backend/src/
├── tools/
│   └── factory.py                  # _register_builtins() calls
│                                   # plugins.core.loader.register_plugin_tools()
├── plugins/
│   ├── core/
│   │   ├── manifest.py             # parse plugin.md, validate schema
│   │   ├── discovery.py            # walk plugins/catalog/*/plugin.md
│   │   └── loader.py               # import tool modules, register BaseTools
│   └── catalog/
│       └── lsp/
│           ├── plugin.md
│           ├── setup.sh            # idempotent; ensures basedpyright present
│           ├── tools/
│           │   ├── __init__.py
│           │   ├── hover.py
│           │   ├── find_definitions.py
│           │   ├── find_references.py
│           │   ├── diagnostics.py
│           │   └── query_symbols.py
│           └── runtime/
│               ├── __init__.py
│               ├── server.py       # @register_plugin_op handlers
│               ├── session_manager.py
│               ├── basedpyright_session.py
│               ├── lsp_jsonrpc.py
│               └── paths.py
└── sandbox/
    ├── plugin/
    │   ├── __init__.py             # exports `call_plugin`
    │   ├── manifest.py             # in-sandbox-aware view of plugin.md
    │   ├── install.py              # bundle upload + setup.sh runner
    │   ├── session.py              # host-side `call_plugin` implementation
    │   ├── projection.py           # generic layer-stack snapshot projection
    │   ├── handler.py              # api.plugin.ensure / api.plugin.status
    │   └── runtime/
    │       ├── __init__.py         # exports `register_plugin_op`, PluginOpContext
    │       └── registry.py         # in-sandbox plugin op registry
    └── runtime/daemon/
        └── handler/plugins.py      # OP_TABLE shim → sandbox.plugin.handler
```

Boundary rules:

- `backend/src/plugins/**` MUST NOT import `sandbox.*`. Catalog tool files and
  catalog runtime modules talk to the sandbox only through
  `sandbox.plugin.call_plugin` (host) and
  `sandbox.plugin.runtime.register_plugin_op` (in-sandbox). The plugin runtime
  can import its own sibling modules.
- `backend/src/sandbox/plugin/**` MUST stay plugin-agnostic. No `lsp/`, no
  language-specific code, no plugin-name string switches. All plugin-specific
  behavior lives under `plugins/catalog/<name>/`.
- Plugin op names use `plugin.<name>.<op>`. The loader rejects manifests whose
  tool names don't match the manifest's `name`, and the in-sandbox registry
  rejects op registrations whose `<name>` part doesn't match.

## 3. Where Manifest Loading Lives

Manifest parsing and tool registration live under `plugins/core/`, not
`tools/plugin_loader.py`. The justification:

- `plugin.md` parsing has zero coupling to the tool system; it is a generic
  catalog reader. Putting it under `plugins/` keeps the catalog
  self-contained, which is what makes it possible for `plugins/**` to remain
  free of `sandbox.*` and `tools.*` imports beyond the public BaseTool surface.
- Registration into the existing `tools.factory` is one line that
  `tools.factory._register_builtins()` calls:

```python
# backend/src/tools/factory.py
from plugins.core.loader import register_plugin_tools

def _register_builtins() -> None:
    from tools.sandbox_toolkit import make_sandbox_tools
    from tools.submission import make_submission_tools
    from tools.subagent import make_subagent_tool_from_context

    _register_many(make_sandbox_tools())
    _register_many(make_submission_tools())
    register_tool_factory("run_subagent", make_subagent_tool_from_context)
    _register_many(register_plugin_tools())  # discovers + imports plugin tools
```

`register_plugin_tools()` returns a `list[BaseTool]` so the existing
`_register_many` path applies unchanged. Failures during manifest parse or
import surface as startup errors with the offending file path.

## 4. Permissioning

Plugin tool names are normal tool names. Agent permissioning is the existing
`allowed_tools` list. There is no new agent-definition field, no per-plugin
permission, and no plugin-level enable flag — selecting `lsp.hover` in
`allowed_tools` is what enables the LSP plugin for that agent.

`AgentDefinitionValidator` continues to validate against the actual registered
tool names and works without modification once `register_plugin_tools()` runs
during builtin registration.

## 5. Install And Routing

`call_plugin(context, plugin, op, payload)` is the only host-side entry point a
tool author touches. Internally it does the following on each call:

1. Resolve `sandbox_id`, `layer_stack_root`, and caller from the tool context.
2. Through `sandbox.plugin.install.ensure_installed(sandbox_id, plugin)`:
   - check the install marker
     `/tmp/eos-sandbox-runtime/plugins/<plugin>/.installed-<bundle-hash>`,
   - if missing, upload the plugin bundle (its `tools/`, optional `runtime/`,
     `setup.sh`, and `plugin.md`) under
     `/tmp/eos-sandbox-runtime/plugins/<plugin>/`,
   - run `setup.sh` once with `EOS_PLUGIN_DIR` set to the upload location,
   - on success, write the marker.
3. Through `sandbox.plugin.session.ensure_runtime_loaded(sandbox_id, plugin)`:
   - call `api.plugin.ensure {"plugin": "<plugin>"}` to ask the daemon to
     import the plugin's `runtime/server.py`. The handler is idempotent and
     succeeds when no runtime is declared.
4. Dispatch `call_daemon_api(sandbox_id, "plugin.<plugin>.<op>", payload)`.
5. Wrap the response in a `ToolResult`.

Install is **lazy on first plugin-tool invocation**, gated by the marker. This
mirrors the existing `runtime_bundle.py` pattern and avoids paying setup cost
for plugins the agent never calls. `ensure_installed` and `ensure_runtime_loaded`
both serialize per `(sandbox_id, plugin)` so concurrent first calls do not
race the setup script.

A future optimization can pre-warm: at agent construction, derive the set of
plugins implied by `allowed_tools` and start `ensure_installed` for each in the
background. The contract above does not depend on it.

## 6. Daemon Surface

Two new daemon ops, both registered through the existing
`sandbox.runtime.daemon.rpc.dispatcher.register_op`:

- `api.plugin.ensure {"plugin": "<name>"}` — imports
  `plugins.catalog.<name>.runtime.server`, which decorates handlers with
  `@register_plugin_op`. The registry in `sandbox.plugin.runtime.registry`
  registers each as `plugin.<name>.<op>` with the dispatcher and refuses
  duplicate identical registrations silently (idempotent), real conflicts loudly.
- `api.plugin.status {}` — returns the loaded plugin names, registered op
  names, and any per-plugin runtime stats (e.g. active LSP sessions, leased
  projections).

The OP_TABLE shim `sandbox.runtime.daemon.handler.plugins` is one file that
imports `sandbox.plugin.handler` and registers `api.plugin.ensure` and
`api.plugin.status` in the dispatcher's existing `_load_peer_bootstraps()`
path. Plugin-specific ops are not registered there; they appear when
`api.plugin.ensure` runs.

There are two unrelated "sessions" — keep them distinct:

```text
host PluginToolSession
  owns: install marker check, ensure call, daemon dispatch
  state: none (per-call)
  lives at: backend/src/sandbox/plugin/session.py

in-sandbox plugin op registry
  owns: plugin op table inside the daemon process
  state: registered handlers, projection leases held by stateful plugins
  lives at: backend/src/sandbox/plugin/runtime/registry.py
```

## 7. Workspace Projection (LSP Constraint)

The workspace truth is the workspace base layer plus the active layer-stack
manifest, not the mutable provider filesystem. LSP needs a real filesystem
tree to point `basedpyright --rootUri` at, so `sandbox/plugin/projection.py`
exposes a generic, lease-backed projection of the active manifest:

- `workspace_projection()` on `PluginOpContext` returns
  `{manifest_key, lowerdir, lease_id}` over the active manifest.
- The lease is held for the life of the consuming plugin session and released
  through the same context on eviction.
- The projection reads `WorkspaceBinding` from `layer_stack_root` and acquires
  a snapshot through `LayerStackManager.prepare_workspace_snapshot(...)`.

The projection module is part of the sandbox plugin adapter, not the LSP
plugin. It must remain plugin-agnostic. LSP is its first consumer; future
stateful plugins (e.g. a Python REPL plugin needing a stable rootdir) reuse it.

## 8. LSP Plugin

Agent-visible tools (declared in `plugin.md`):

- `lsp.find_definitions`
- `lsp.find_references`
- `lsp.hover`
- `lsp.diagnostics`
- `lsp.query_symbols`

`setup.sh` responsibilities (idempotent):

- verify a Python interpreter that meets `basedpyright`'s requirement,
- install `basedpyright` and `basedpyright-langserver` if not already present,
- write a marker so re-runs are cheap.

`runtime/server.py` responsibilities (one `@register_plugin_op` per tool):

- delegate to `runtime/session_manager.py`, which keys long-lived
  `basedpyright` sessions by `(layer_stack_root, manifest_key)`,
- evict and restart the session when the manifest key changes,
- release the projection lease on eviction.

`runtime/basedpyright_session.py`:

- own the `basedpyright-langserver` stdio child,
- send LSP `initialize`/`textDocument/didOpen`/request,
- expose typed helpers `hover`, `find_definitions`, `find_references`,
  `diagnostics`, `query_symbols` that return JSON-safe dicts,
- timeout and one reconnect path on stdio failure.

`runtime/paths.py`:

- map agent-facing paths (repo-relative or workspace-absolute) onto the
  projection's `lowerdir` so document URIs are
  `file://<lowerdir>/<layer_path>`,
- map returned URIs back to agent-facing paths in the response.

`runtime/lsp_jsonrpc.py` owns the wire protocol details (framing, request
ids, JSON-RPC error decoding). It exists to keep `basedpyright_session.py`
focused on LSP semantics.

The LSP plugin must not run `basedpyright` against the provider workspace
directory for workspace files — that path can be stale relative to committed
layer-stack mutations. It may still use the sandbox's installed Python
interpreter and dependencies for type resolution.

LSP is read-only in v1. Edit-producing LSP features (`WorkspaceEdit`) are
deferred: their snapshot URIs would need to be mapped back to layer paths and
applied through existing OCC-gated `write_file`/`edit_file` paths, never by
mutating the snapshot or `/testbed` directly.

## 9. Selected-Tool Install Derivation

The bundle uploaded under `/tmp/eos-sandbox-runtime/plugins/<plugin>/` is
per-plugin, not per-bundle-of-all-plugins. That is what makes lazy install
cheap: when the agent never calls a plugin tool, that plugin's files never
land in the sandbox.

A future eager-warmup path can compute the plugin set from the agent's
`allowed_tools` (split each name on the first `.`, look up the manifest) and
parallelize `ensure_installed` for each. The lazy-on-first-call path is the
contract; eager warmup is an optimization.

## 10. Execution Sequence

1. Add `plugins/core/manifest.py` (parser + schema validation) and
   `plugins/core/discovery.py` (walk `plugins/catalog/*/plugin.md`).
2. Add `plugins/core/loader.py:register_plugin_tools()` returning
   `list[BaseTool]`. Wire into `tools.factory._register_builtins()`.
3. Add `sandbox/plugin/install.py:ensure_installed()` with marker file
   `/tmp/eos-sandbox-runtime/plugins/<plugin>/.installed-<hash>`. Hash covers
   `plugin.md`, every file in `tools/`, every file in `runtime/`, and
   `setup.sh`. Per-`(sandbox_id, plugin)` async lock.
4. Add `sandbox/plugin/session.py:call_plugin()` implementing the §5 sequence,
   plus `sandbox/plugin/handler.py` with `api.plugin.ensure` and
   `api.plugin.status`. Add the OP_TABLE shim in
   `sandbox/runtime/daemon/handler/plugins.py`.
5. Add `sandbox/plugin/runtime/registry.py` with `register_plugin_op` decorator
   and the `PluginOpContext` shape. The decorator queues registrations at
   import time; `api.plugin.ensure` flushes them into the daemon dispatcher.
6. Add `sandbox/plugin/projection.py` exposing a lease-backed
   `WorkspaceProjection` over `LayerStackManager`. Surface it through
   `PluginOpContext.workspace_projection()`.
7. Implement the LSP catalog: `plugin.md`, `setup.sh`, the five
   `tools/<tool>.py` files (each ≤30 lines), and the `runtime/` modules.
8. Run a live LSP spike against a real sandbox:
   - sandbox with manifest version `>= 1`,
   - first `lsp.hover` call triggers install + `setup.sh` + ensure,
   - call hover/definitions/references/diagnostics/symbols,
   - edit a file via existing `write_file`,
   - call hover again and assert the response reflects the new manifest, not
     a cached snapshot.
9. Defer edit-producing LSP features. Document the plan to translate
   `WorkspaceEdit` snapshot URIs back to layer paths and apply through
   existing write/edit paths so OCC remains the mutation gate.

## 11. Tests

Focused unit tests:

- `plugin.md` parser rejects: missing `name`, `name` mismatching directory,
  duplicate plugin names, tool name not prefixed with `<name>.`, tool module
  path escaping the plugin dir, missing tool module, tool module that does
  not export a `BaseTool` whose `.name` matches.
- Discovery ignores catalog folders without `plugin.md`.
- LSP `plugin.md` produces exactly five `BaseTool` instances with names
  matching the manifest.
- Catalog tool modules import without starting basedpyright or other native
  processes (host-importable check).
- `register_plugin_tools()` integrates with `tools.factory` so
  `create_tool("lsp.hover", ctx)` returns the right instance.
- `ensure_installed` is idempotent: a second call with the same content hash
  is a no-op (no upload, no `setup.sh` re-run).
- `ensure_installed` re-uploads when any of `plugin.md`, `tools/*`,
  `runtime/*`, or `setup.sh` changes (hash covers them all).
- `call_plugin` serializes concurrent first invocations on the same
  `(sandbox_id, plugin)` so `setup.sh` runs exactly once.
- `api.plugin.ensure` registers the expected `plugin.<name>.<op>` set into
  the daemon dispatcher and is idempotent under repeat calls.
- `register_plugin_op("lsp", "hover")` is rejected by the in-sandbox registry
  if attempted by a module outside `plugins.catalog.lsp.runtime`.
- `WorkspaceProjection` reads the active manifest, not `workspace_root`, and
  releases its lease when the consuming session is evicted.
- LSP path mapping uses snapshot URIs; cached LSP session is invalidated when
  the manifest version/root changes.

Focused live test (spike, then keep as a smoke test):

```text
public sandbox API write_file("pkg/mod.py", initial)
  -> lsp.hover("pkg/mod.py", line=L, character=C) → assert symbol info
  -> public sandbox API edit_file("pkg/mod.py", change_signature)
  -> lsp.hover(same coords) → assert response reflects the edit
```

## 12. Risks

| Risk | Mitigation |
|------|------------|
| Author surface drifts back into PluginSpec ceremony | Keep tools as plain `BaseTool`s with a one-import (`call_plugin`) routing helper; review every framework addition against the §1.2 example. |
| `plugin.md` schema grows ad-hoc fields | Treat the schema in §1.1 as the contract; add fields only with a written use case. |
| Setup is slow or flaky | Idempotent, marker-driven, lazy-on-first-call. Concurrent first calls share one install via per-`(sandbox_id, plugin)` lock. |
| Daemon op table becomes a dumping ground | Plugin ops are namespaced `plugin.<name>.<op>`; only `api.plugin.ensure` and `api.plugin.status` live in the static OP_TABLE. |
| basedpyright sees stale files | Project the active manifest into a leased lowerdir; key sessions on `(layer_stack_root, manifest_key)`; never use provider `workspace_root` for workspace files. |
| Snapshot leases leak | Lease lifetime tied to LSP session lifetime; eviction releases lease; `api.plugin.status` exposes active leases for diagnosis. |
| `sandbox/plugin` becomes plugin-specific | No `lsp/` or language-specific code under `sandbox/plugin/`; it stays a generic install + dispatch + projection adapter. |
| Plugin tools leak provider details | Tool authors call only `call_plugin`; provider-specific imports stay inside the existing sandbox adapter. |
| Plugin runtime bypasses OCC | LSP runtime is read-only. Future mutating plugin ops MUST call existing `write_file`/`edit_file`/`shell` paths so OCC remains the mutation gate. |

## 13. Out Of Scope

- External plugin distribution (third-party plugins outside the repo).
- Multi-language LSP support (Python via basedpyright is v1).
- A separate MCP server exposed to the host.
- Host-managed plugin lifecycle hooks.
- Per-plugin permissions distinct from `allowed_tools`.
- Plugin writes that bypass OCC.
- Eager pre-warm of plugin install at agent construction (lazy on first call
  is the v1 contract; eager warmup is a follow-up optimization).
