# Sandbox Rename — `control/` + `runtime/` → `host/` + `daemon/`

**Status:** draft migration plan
**Source:** `backend/src/sandbox/control` and `backend/src/sandbox/runtime`
**Depends on:** `per-call-snapshot-layer-stack-migration` phases 05–08 landed (do not start while that migration is mid-flight)

## 1. Task Specification

Rename the two sandbox top-level packages so the host-vs-in-sandbox boundary is
visible from the layout. Today `control/` (host-side RPC + deploy + ops) and
`runtime/` (in-sandbox dispatcher + handlers + CLI) sound like overlapping
concerns; in fact they are the two sides of one RPC and never share a process.
The rename also fixes name collisions inside both trees (`server.py` /
`daemon.py` / `*_server.py` / `*_handlers.py` / `clients/`) and lifts the
admittedly-misplaced `async_bridge.py` out of the in-sandbox tree.

Implementation scope:

```text
rename sandbox.control       -> sandbox.host    (host-side, runs in orchestrator)
rename sandbox.runtime       -> sandbox.daemon  (in-sandbox, shipped via bundle)
introduce non-flat subpackages on both sides (deploy/, rpc/, handlers/, services/)
lift sandbox.runtime.async_bridge -> sandbox.utils.async_bridge
update bundle builder to ship sandbox/daemon/ instead of sandbox/runtime/
update `python -m sandbox.runtime.daemon` launcher -> `python -m sandbox.daemon`
update test_import_fence.py path/prefix references
move test_runtime/ -> test_daemon/, test_control/ -> test_host/
```

Out of scope:

```text
no behavior change in any handler
no protocol or wire-format change
no merge of host and daemon (they CANNOT merge — different processes)
no cleanup of the per-call-snapshot migration's planning docs
```

Exit condition:

```text
sandbox/control/ and sandbox/runtime/ no longer exist; the two-side boundary
is grep-able from directory names; full test suite green; bundle still uploads
and the daemon still launches in a live sandbox; import_fence locks the new
paths.
```

## 2. Final Layout

```text
sandbox/host/                       runs in orchestrator process
+-- deploy/
|   +-- bundle.py                   was control/daemon/bundle.py
|   +-- peer_setup.py               was control/daemon/install.py
+-- rpc/
|   +-- client.py                   was control/daemon/command.py
|                                   class renamed: RuntimeCommandClient -> DaemonClient
+-- ops/                            unchanged contents
    +-- setup.py
    +-- recovery.py
    +-- git.py
    +-- workspace.py
    +-- context.py

sandbox/daemon/                     runs INSIDE the sandbox
+-- __main__.py                     was runtime/daemon.py
|                                   `python -m sandbox.daemon`
+-- rpc/
|   +-- server.py                   serve() + _handle_connection
|   |                               (lifted from runtime/daemon.py body)
|   +-- dispatcher.py               was runtime/server.py (OP_TABLE + dispatch)
+-- handlers/
|   +-- api.py                      was runtime/api_handlers.py
|   +-- shell.py                    was runtime/command_exec_server.py
|   +-- workspace.py                was runtime/layer_stack_handlers.py
|   +-- _common.py                  shared helpers from Phase 2 dedup
+-- services/
|   +-- layer_stack.py              merged runtime/layer_stack_server.py
|   |                               + runtime/clients/layer_stack.py
|   +-- workspace_binding.py        was runtime/clients/occ.py
+-- overlay_shell/                  unchanged

sandbox/utils/
+-- async_bridge.py                 was runtime/async_bridge.py
                                    (the existing TODO)
```

## 3. Phased Migration

Each phase is one atomic commit; the codebase compiles and tests pass after
each. Phases are independently revertable. Codex-parallel safety: Phases 1–2
don't move directories, so they're safe to run mid-stream against an active
codex session in `runtime/`. Phases 3–4 require a clean working tree.

### Phase 1 — Lift `async_bridge.py` to `sandbox/utils/`

Lowest-risk move; already flagged as misplaced by `runtime/__init__.py`.

```text
git mv backend/src/sandbox/runtime/async_bridge.py \
       backend/src/sandbox/utils/async_bridge.py
```

Importers to rewrite (5 host-side files):

```text
sandbox/control/ops/setup.py
sandbox/control/ops/recovery.py
sandbox/control/ops/git.py
sandbox/control/daemon/command.py
sandbox/runtime/command_exec_server.py
```

Plus `tests/unit_test/test_sandbox/test_async/test_bridge.py`.

Update `runtime/__init__.py` to drop the TODO line.

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_async -q
uv run ruff check backend/src/sandbox
```

### Phase 2 — Dedup inside `runtime/` (no directory rename yet)

Pull duplicated helpers into one module so Phase 3's mass move is smaller.

New: `backend/src/sandbox/runtime/_handler_common.py`

```text
build_runtime_services(layer_stack_root) -> (LayerStackClient, OccService, oracle)
gitignore_timings(oracle) -> dict[str, float]
conflict_to_dict(conflict) -> dict | None
drop_runtime_services_cache(layer_stack_root) -> None
```

Rewrite `api_handlers.py` and `command_exec_server.py` to consume it.
Collapse the two `_SERVICE_CACHE` dicts into one keyed by `layer_stack_root`.
Delete `_drop_peer_runtime_caches` from `layer_stack_handlers.py` and have it
call the new shared dropper.

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_runtime -q
uv run pytest backend/tests/unit_test/test_sandbox/test_api -q
```

### Phase 3 — Rename `runtime/` -> `daemon/` (single atomic commit)

Working-tree precondition: clean (no codex sessions touching `runtime/`).

File moves:

```text
runtime/                          ->  daemon/
runtime/daemon.py serve() body    ->  daemon/rpc/server.py
runtime/daemon.py main() entry    ->  daemon/__main__.py
runtime/server.py                 ->  daemon/rpc/dispatcher.py
runtime/api_handlers.py           ->  daemon/handlers/api.py
runtime/command_exec_server.py    ->  daemon/handlers/shell.py
runtime/layer_stack_handlers.py   ->  daemon/handlers/workspace.py
runtime/_handler_common.py        ->  daemon/handlers/_common.py
runtime/layer_stack_server.py  +  }
runtime/clients/layer_stack.py    ->  daemon/services/layer_stack.py  (merged)
runtime/clients/occ.py            ->  daemon/services/workspace_binding.py
runtime/overlay_shell/            ->  daemon/overlay_shell/
```

Update `daemon/rpc/dispatcher.py:_load_peer_bootstraps` to import from the new
handler paths.

Update `host/deploy/bundle.py` (still at old path until Phase 4):

```text
_add_python_tree(tar, sandbox_dir / "runtime", ...)
   -> _add_python_tree(tar, sandbox_dir / "daemon", ...)
```

Update `control/daemon/command.py` launcher:

```text
nohup "$py" -m sandbox.runtime.daemon ...
   -> nohup "$py" -m sandbox.daemon ...
```

Update `test_import_fence.py`:

```text
SRC_ROOT / "sandbox" / "runtime"   ->  SRC_ROOT / "sandbox" / "daemon"
"sandbox.runtime"                   ->  "sandbox.daemon"
"sandbox.runtime.layer_stack_server" -> "sandbox.daemon.services.layer_stack"
"sandbox.runtime.clients.occ"        -> "sandbox.daemon.services.workspace_binding"
test_runtime_code_does_not_import_daytona_provider_modules
   -> test_daemon_code_does_not_import_daytona_provider_modules
```

Test directory move:

```text
backend/tests/unit_test/test_sandbox/test_runtime/  ->  test_daemon/
```

Importer rewrite across ~30 source + test files (full list from grep):

```text
sandbox/__init__.py
sandbox/api/facade.py
sandbox/api/status/__init__.py
sandbox/api/tool/_runtime.py
sandbox/control/{daemon/{bundle,command,install,__init__},ops/{git,setup,recovery,__init__,context,workspace},__init__}.py
sandbox/occ/{service,orchestrator}.py
sandbox/overlay/runner/runtime_invoker.py
sandbox/providers/{daytona/{client/async_,context},protocol}.py
plus all tests under test_sandbox/ that import sandbox.runtime
```

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox -q
uv run ruff check backend/src/sandbox backend/tests
grep -r "sandbox\.runtime\|sandbox/runtime" backend/src backend/tests   # must return zero hits
python -c "import sandbox.daemon"                                       # must succeed
```

Live verification (one-off, do not commit):

```text
spawn a fresh sandbox, run setup_after_create, confirm
runtime daemon binds /tmp/eos-sandbox-runtime/runtime.sock and one shell call
round-trips. The launcher rewrite is the only piece that touches sandbox-side
behavior; everything else is pure import surgery.
```

### Phase 4 — Rename `control/` -> `host/` (single atomic commit)

File moves:

```text
control/                              ->  host/
control/daemon/bundle.py              ->  host/deploy/bundle.py
control/daemon/install.py             ->  host/deploy/peer_setup.py
control/daemon/command.py             ->  host/rpc/client.py
class RuntimeCommandClient            ->  class DaemonClient
control/daemon/__init__.py            ->  delete (replaced by deploy/, rpc/)
control/ops/                          ->  host/ops/                  (verbatim move)
```

Update `test_import_fence.py`:

```text
SRC_ROOT / "sandbox" / "control"   ->  SRC_ROOT / "sandbox" / "host"
"sandbox.control"                   ->  "sandbox.host"
test_control_runtime_api_*          ->  test_host_daemon_api_*       (rename only)
```

Test directory move:

```text
backend/tests/unit_test/test_sandbox/test_control/  ->  test_host/
```

Importer rewrite across ~6 source + tests files:

```text
sandbox/api/{facade,tool/_runtime,status/__init__}.py
sandbox/providers/daytona/context.py    (only if it imports control)
plus tests
```

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox -q
uv run ruff check backend/src/sandbox backend/tests
grep -r "sandbox\.control\|sandbox/control" backend/src backend/tests   # must return zero hits
```

### Phase 5 — Cleanup pass (single atomic commit)

```text
delete empty cache dirs (__pycache__) and stray .DS_Store files
update planning docs that reference old paths if they're still active
   (skip archived phase docs — historical record)
update any docstrings inside moved modules that still say "runtime" / "control"
```

Verify:

```text
full uv run pytest backend/tests -q
uv run ruff check backend
mypy / pyright if configured
one live e2e smoke (a single shell + write + read round-trip)
```

## 4. Risk Mitigations

| Risk | Mitigation |
|---|---|
| Codex parallel session edits `runtime/api_handlers.py` mid-rename | Run Phases 3–4 only with a clean working tree; coordinate with the codex session to flush its commit first |
| `test_import_fence.py` breaks silently because path strings are stale | Update fence test in the same commit as the directory rename; CI catches drift |
| `python -m sandbox.runtime.daemon` launcher persists in already-deployed sandboxes | The bundle hash (`bundle.py:bundle_hash`) changes when `daemon/` source changes; `ensure_runtime_uploaded` re-uploads on hash mismatch and the next call rewrites the launcher |
| `per-call-snapshot-layer-stack-migration` phase 05+ docs reference `runtime.*` paths and become misleading | Do not start this rename until phase 08 lands; then `sed`-update the active phase docs in Phase 5 |
| In-sandbox bundle still has files at `sandbox/runtime/...` after host upgrade | `_BUNDLE_HASH_MARKER` invalidates on hash change; first call after deploy re-extracts the new tree before any handler runs |
| Test discovery in `test_runtime/` and `test_control/` referenced by configs | Update `pytest.ini` / `pyproject.toml` / CI workflows in the rename commit |

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `sandbox/host/` | Runs in the orchestrator. The "control plane" framing was generic; "host" matches container/VM convention and makes the boundary unambiguous. |
| `sandbox/daemon/` | Runs inside the sandbox as a long-lived process. Matches existing internal terminology ("the runtime daemon"). |
| `host/rpc/client.py` + `daemon/rpc/server.py` | Symmetric RPC vocabulary: client lives on the host, server lives in the daemon. Searchable from either side. |
| `daemon/rpc/dispatcher.py` | The OP_TABLE lookup. Today called `server.py` but it doesn't serve — the AF_UNIX server does. |
| `daemon/__main__.py` | `python -m sandbox.daemon` is shorter and idiomatic; replaces `python -m sandbox.runtime.daemon`. |
| `daemon/handlers/` | OP_TABLE entries: parse args, format response, emit timings. |
| `daemon/services/` | In-process service objects the handlers delegate to (was a confused mix of `*_server.py` and `clients/`). |
| `host/deploy/` | Bundle build, bundle upload, peer setup script registry — everything that gets the daemon into a sandbox. |
| `DaemonClient` (was `RuntimeCommandClient`) | Names the thing it talks to, not what it sends. Symmetric with `daemon/rpc/server.py`. |
| `sandbox/utils/async_bridge.py` | Used by host AND in-sandbox code; never belonged under `runtime/`. The existing TODO finally retired. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox -q
uv run pytest backend/tests/unit_test/test_sandbox/test_import_fence.py -q
uv run ruff check backend/src/sandbox backend/tests
grep -r "sandbox\.runtime\|sandbox\.control" backend/src backend/tests | wc -l   # 0
grep -r "sandbox/runtime\|sandbox/control" backend/src backend/tests | wc -l     # 0
python -c "import sandbox.host; import sandbox.daemon; import sandbox.utils.async_bridge"
```

Required assertions after Phase 5:

- `sandbox/control/` and `sandbox/runtime/` directories no longer exist
- import-fence test asserts no module imports `sandbox.runtime.*` or `sandbox.control.*`
- bundle builder tars `sandbox/daemon/` and the bundle hash changes
- `host/rpc/client.py:DaemonClient` launches the daemon via `python -m sandbox.daemon`
- a live shell + write + read round-trip succeeds against a freshly redeployed bundle
- no production code imports `sandbox.utils.async_bridge` from inside `daemon/` (it's a host-side concern; the daemon's resident asyncio loop runs handlers directly)
