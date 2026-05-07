# Sandbox Rename — `control/` + `runtime/` → `host/` + `daemon/`

**Status:** redraft against current `main`
**Source:** `backend/src/sandbox/control/` and `backend/src/sandbox/runtime/`
**Supersedes:** prior draft (pre-OCC-backend-factory, pre-`handlers/` package)
**Depends on:** per-call-snapshot-layer-stack-migration phases 05–08 landed (already in)

## 1. Task Specification

Rename the two sandbox top-level packages so the host-vs-in-sandbox boundary is
visible from the layout. Today `control/` (host-side RPC + deploy + ops) and
`runtime/` (in-sandbox dispatcher + handlers + service code) sound like
overlapping concerns; in fact they are the two sides of one RPC and never
share a process. The rename also cleans up the file-naming asymmetry inside
`runtime/` (`server.py` / `daemon.py` / `*_server.py` / `*_handlers.py` /
`clients/`) and lifts the long-misplaced `async_bridge.py` out of the
in-sandbox tree.

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
no merge of host and daemon (CANNOT merge — different processes)
no rename of api.tool._runtime  (host-side thin client; cosmetic only)
no rework of OCC backend factory or handlers/_common (already done)
```

Exit condition:

```text
sandbox/control/ and sandbox/runtime/ no longer exist; the two-side
boundary is grep-able from directory names; full test suite green; bundle
still uploads and the daemon still binds AF_UNIX in a live sandbox;
import_fence locks the new paths.
```

## 2. Final Layout

```text
sandbox/host/                            runs in orchestrator process
+-- deploy/
|   +-- bundle.py                        was control/daemon/bundle.py
+-- rpc/
|   +-- client.py                        was control/daemon/command.py
+-- ops/                                 verbatim move (contents unchanged)
    +-- setup.py
    +-- recovery.py
    +-- git.py
    +-- workspace.py
    +-- context.py

sandbox/daemon/                          runs INSIDE the sandbox
+-- __main__.py                          was runtime/daemon.py main()
|                                        `python -m sandbox.daemon`
+-- rpc/
|   +-- server.py                        AF_UNIX serve()/accept loop, lifted
|   |                                    out of runtime/daemon.py body
|   +-- dispatcher.py                    was runtime/server.py (OP_TABLE +
|                                        validate + dispatch)
+-- handlers/                            OP_TABLE entry modules
|   +-- _common.py                       unchanged file (shared scaffolding)
|   +-- edit.py                          was handlers/edit_handler.py
|   +-- metrics.py                       was handlers/metrics_handler.py
|   +-- read.py                          was handlers/read_handler.py
|   +-- write.py                         was handlers/write_handler.py
|   +-- shell.py                         was handlers/shell_handler.py
|   +-- workspace.py                     was layer_stack_handlers.py
|   +-- health.py                        was health_handlers.py
+-- services/                            in-process objects handlers delegate to
|   +-- workspace_server.py              was layer_stack_server.py
|   |                                    (LayerStackWorkspaceServer + manager
|   |                                     cache + fence_stale_staging)
|   +-- layer_stack_client.py            was clients/layer_stack.py
|   |                                    (LayerStackClient OCC-port adapter)
|   +-- workspace_binding.py             was clients/occ.py
|   |                                    (RuntimeWorkspaceBindingReader)
|   +-- occ_backend.py                   was occ_server.py
|   |                                    (OccBackend factory + cache)
|   +-- shell_runner.py                  was command_exec_server.py
+-- overlay_shell/                       verbatim move
    +-- __init__.py
    +-- cli.py

sandbox/utils/
+-- async_bridge.py                      was runtime/async_bridge.py
                                         (the existing TODO finally retired)
```

Naming notes:

- `services/workspace_server.py` keeps the `_server` suffix to disambiguate
  from `handlers/workspace.py` (the OP_TABLE entry module). Greps and stack
  traces stay legible without a "which workspace.py?" guessing game.
- `services/layer_stack_client.py` keeps the `_client` suffix for the same
  reason — it's the OCC-port adapter, not the manager cache.
- `services/occ_backend.py` over `services/occ.py` because the module owns
  the `OccBackend` dataclass and `build_occ_backend` factory, and `occ` as a
  bare name collides with `sandbox.occ`.

## 3. Phased Migration

Each phase is one atomic commit; codebase compiles and tests pass after
each. Phases are independently revertable. The plan deliberately keeps
phases 2 and 4 as flat renames first, then internal restructure as
phases 3 and 5 — smaller blast radius per commit, each phase rollback-able
without touching the other side.

### Phase 1 — Lift `async_bridge.py` to `sandbox/utils/`

Lowest-risk move; already flagged as misplaced by `runtime/__init__.py`.

```text
git mv backend/src/sandbox/runtime/async_bridge.py \
       backend/src/sandbox/utils/async_bridge.py
mkdir -p backend/src/sandbox/utils && touch backend/src/sandbox/utils/__init__.py
```

Importers to rewrite (8 source files, verified via grep):

```text
sandbox/control/ops/setup.py            (3 occurrences)
sandbox/control/ops/recovery.py
sandbox/control/ops/git.py
sandbox/runtime/command_exec_server.py
sandbox/overlay/runner/runtime_invoker.py
sandbox/occ/service.py                  ← layering violation today
sandbox/occ/orchestrator.py             ← layering violation today
sandbox/providers/daytona/client/async_.py
```

The `occ/service.py` and `occ/orchestrator.py` imports are exactly the
layering violation the lift exists to fix — `occ/` should never reach into
`runtime/`. Calling these out so reviewers see the motivation, not just the
mechanical move.

Plus tests:

```text
backend/tests/unit_test/test_sandbox/test_async/test_bridge.py
```

Update `runtime/__init__.py` to drop the `async_bridge.py remains here
pending a separate refactor` paragraph.

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox/test_async -q
uv run ruff check backend/src/sandbox
grep -rn "sandbox\.runtime\.async_bridge" backend/src backend/tests   # 0
```

### Phase 2 — `runtime/` → `daemon/` (flat rename, atomic commit)

Working-tree precondition: clean.

This phase keeps the existing internal layout (no move into `rpc/`,
`services/` yet). The atomic unit is "every importer of `sandbox.runtime.*`
is now `sandbox.daemon.*`." Internal restructure happens in Phase 3.

File moves:

```text
runtime/                          ->  daemon/
runtime/__init__.py               ->  daemon/__init__.py
runtime/daemon.py                 ->  daemon/daemon.py            (will split in P3)
runtime/server.py                 ->  daemon/server.py            (-> rpc/dispatcher.py in P3)
runtime/handlers/                 ->  daemon/handlers/            (verbatim)
runtime/health_handlers.py        ->  daemon/health_handlers.py   (-> handlers/health.py in P3)
runtime/layer_stack_handlers.py   ->  daemon/layer_stack_handlers.py (-> handlers/workspace.py in P3)
runtime/layer_stack_server.py     ->  daemon/layer_stack_server.py
runtime/command_exec_server.py    ->  daemon/command_exec_server.py
runtime/occ_server.py             ->  daemon/occ_server.py
runtime/clients/                  ->  daemon/clients/
runtime/overlay_shell/            ->  daemon/overlay_shell/
```

Code edits inside the tree (all simple textual rewrites of
`sandbox.runtime` → `sandbox.daemon`):

```text
daemon/__init__.py                — docstring
daemon/daemon.py                  — module docstring + module-name args
                                    + parser prog + logger name
daemon/server.py                  — module docstring + _load_peer_bootstraps
daemon/handlers/__init__.py       — re-exports
daemon/handlers/_common.py        — imports from daemon.occ_server etc.
daemon/handlers/edit_handler.py   — imports
daemon/handlers/metrics_handler.py — imports
daemon/handlers/read_handler.py   — imports
daemon/handlers/shell_handler.py  — imports
daemon/handlers/write_handler.py  — imports
daemon/health_handlers.py         — imports + module-doc
daemon/layer_stack_handlers.py    — imports
daemon/layer_stack_server.py      — module-doc
daemon/command_exec_server.py     — imports
daemon/occ_server.py              — module-doc + imports
daemon/clients/__init__.py        — docstring
daemon/clients/layer_stack.py     — imports
daemon/clients/occ.py             — module-doc
```

Bundle / launcher updates (host side, still at old `control/` path until P4):

```text
control/daemon/bundle.py
  - sandbox_dir / "runtime"          -> sandbox_dir / "daemon"
  - rename _RUNTIME_EXCLUDE_PARTS     -> _DAEMON_EXCLUDE_PARTS

control/daemon/command.py
  - "python -m sandbox.runtime.daemon" -> "python -m sandbox.daemon"
                                          (single string in launcher heredoc)
```

Importer rewrite across ~25 source + test files (full enumeration):

```text
sandbox/__init__.py                                 (top-level docstring)
sandbox/api/facade.py                               (NB: imports control.ops)
sandbox/api/status/__init__.py                      (mentions runtime in docs)
sandbox/api/tool/_runtime.py                        (imports control.daemon.*)
sandbox/control/daemon/bundle.py                    (paths + exclude name)
sandbox/control/daemon/command.py                   (launcher string)
sandbox/control/ops/setup.py                        (no runtime import here
                                                     once async_bridge moves
                                                     to sandbox.utils in P1)
sandbox/providers/daytona/context.py                (control imports only)
sandbox/providers/protocol.py                       (docstring mention)
sandbox/occ/service.py                              (cleared by P1)
sandbox/occ/orchestrator.py                         (cleared by P1)
sandbox/overlay/runner/runtime_invoker.py           (overlay_shell import)
sandbox/providers/daytona/client/async_.py          (cleared by P1)
plus all test_sandbox/* files importing sandbox.runtime
```

`test_import_fence.py` updates:

```text
SRC_ROOT / "sandbox" / "runtime"        -> SRC_ROOT / "sandbox" / "daemon"
"sandbox.runtime"                        -> "sandbox.daemon"
"sandbox.runtime.layer_stack_server"     -> "sandbox.daemon.layer_stack_server"
"sandbox.runtime.clients.occ"            -> "sandbox.daemon.clients.occ"
test_runtime_code_does_not_import_*      -> test_daemon_code_does_not_import_*
test_control_runtime_api_*               -> NO change yet (control still control)
                                            (renamed in Phase 4)
```

`test_routing_invariants.py` updates:

```text
"sandbox.runtime.occ_server"            -> "sandbox.daemon.occ_server"
"sandbox.runtime.occ_handlers"          -> "sandbox.daemon.occ_handlers"
"sandbox.runtime.write_edit_handlers"   -> "sandbox.daemon.write_edit_handlers"
"sandbox.runtime.api_handlers"          -> "sandbox.daemon.api_handlers"
```

`test_bundle_upload.py` is the densest test churn — 15+ hardcoded
`"sandbox/runtime/..."` path strings in the expected-bundle assertions
plus the launcher-string assertion. Easy to miss; treat as its own
sub-task inside Phase 2 and grep all `"sandbox/runtime"` and
`"sandbox.runtime"` occurrences in the file.

Test directory move:

```text
backend/tests/unit_test/test_sandbox/test_runtime/  ->  test_daemon/
```

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox -q
uv run pytest backend/tests/unit_test/test_sandbox/test_import_fence.py -q
uv run ruff check backend/src/sandbox backend/tests
grep -rn "sandbox\.runtime\|sandbox/runtime" backend/src backend/tests   # 0
python -c "import sandbox.daemon; sandbox.daemon"                        # ok
```

Live verification (one-off, do not commit):

```text
spawn a fresh sandbox, run setup_after_create, confirm:
- bundle hash differs from pre-rename (because the tree changed)
- daemon binds /tmp/eos-sandbox-runtime/runtime.sock under
  python -m sandbox.daemon
- one shell + one write + one read round-trip
The launcher-string rewrite is the only sandbox-side behavior change;
everything else is import surgery. The pre-existing
_BUNDLE_HASH_MARKER guarantees the new tree is re-extracted.
```

### Phase 3 — Restructure `daemon/` into `rpc/` + `handlers/` + `services/`

All moves stay inside `daemon/`; importers outside `daemon/` (already
rewritten in P2) are unaffected unless they reach into `daemon` internals.

File splits:

```text
daemon/daemon.py                       split into:
  daemon/rpc/server.py                 serve(), _handle_connection,
                                       _prepare_socket_path, _write_pid,
                                       _remove_pid, DEFAULT_*_PATH
  daemon/__main__.py                   main() + argparse entrypoint
  -- delete daemon/daemon.py
```

File renames:

```text
daemon/server.py                       ->  daemon/rpc/dispatcher.py
                                            (OP_TABLE, register_op,
                                             dispatch_envelope*, _validate_*,
                                             _attach_runtime_boot_timings)

daemon/handlers/edit_handler.py        ->  daemon/handlers/edit.py
daemon/handlers/metrics_handler.py     ->  daemon/handlers/metrics.py
daemon/handlers/read_handler.py        ->  daemon/handlers/read.py
daemon/handlers/shell_handler.py       ->  daemon/handlers/shell.py
daemon/handlers/write_handler.py       ->  daemon/handlers/write.py
daemon/health_handlers.py              ->  daemon/handlers/health.py
daemon/layer_stack_handlers.py         ->  daemon/handlers/workspace.py

daemon/layer_stack_server.py           ->  daemon/services/workspace_server.py
daemon/command_exec_server.py          ->  daemon/services/shell_runner.py
daemon/occ_server.py                   ->  daemon/services/occ_backend.py
daemon/clients/layer_stack.py          ->  daemon/services/layer_stack_client.py
daemon/clients/occ.py                  ->  daemon/services/workspace_binding.py
-- delete daemon/clients/__init__.py and the empty clients/ dir
```

In-tree edits (all import rewrites or one-line module-doc updates):

```text
daemon/__init__.py
daemon/__main__.py                     imports rpc.server.serve, etc.
daemon/rpc/__init__.py                 new file
daemon/rpc/server.py                   imports rpc.dispatcher
daemon/rpc/dispatcher.py               _load_peer_bootstraps imports the
                                       new handler paths
daemon/handlers/__init__.py            re-exports the 5 verb handlers
                                       under their new module names
daemon/handlers/_common.py             from daemon.services.occ_backend
                                       import OccBackend, build_occ_backend
daemon/handlers/edit.py                imports
daemon/handlers/metrics.py             imports
daemon/handlers/read.py                imports
daemon/handlers/shell.py               from daemon.services import shell_runner
daemon/handlers/write.py               imports
daemon/handlers/workspace.py           imports services.workspace_server
daemon/handlers/health.py              imports services.workspace_server,
                                       services.shell_runner, services.occ_backend
daemon/services/__init__.py            new file
daemon/services/workspace_server.py    imports
daemon/services/shell_runner.py        imports
daemon/services/occ_backend.py         imports services.layer_stack_client,
                                       services.workspace_binding,
                                       services.workspace_server
daemon/services/layer_stack_client.py  imports services.workspace_server
daemon/services/workspace_binding.py   imports
```

Plus the OP_TABLE registration table inside `rpc/dispatcher.py`:

```text
from sandbox.daemon.handlers import (
    edit, health, metrics, read, shell, workspace, write,
)
from sandbox.overlay.handlers import run as overlay_run

OP_TABLE entries:
    api.edit_file                    -> edit.edit_file
    api.layer_metrics                -> metrics.layer_metrics
    api.read_file                    -> read.read_file
    api.shell                        -> shell.shell
    api.write_file                   -> write.write_file
    api.runtime.ready                -> health.runtime_ready
    api.ensure_workspace_base        -> workspace.ensure_workspace_base
    api.build_workspace_base         -> workspace.build_workspace_base
    api.prepare_workspace_snapshot   -> workspace.prepare_workspace_snapshot
    api.release_workspace_snapshot   -> workspace.release_workspace_snapshot
    api.layer_stack.fence_stale_staging -> workspace.fence_stale_staging
    api.workspace_binding            -> workspace.workspace_binding
    overlay.run                      -> overlay_run.handle
```

Bundle path adjustment:

```text
control/daemon/bundle.py  (still at old path; renamed in P4)
  - test_bundle_upload.py expectations now list new paths under
    sandbox/daemon/rpc/, sandbox/daemon/handlers/, sandbox/daemon/services/
```

`test_import_fence.py` deeper-path strings:

```text
"sandbox.daemon.layer_stack_server"     -> "sandbox.daemon.services.workspace_server"
"sandbox.daemon.clients.occ"            -> "sandbox.daemon.services.workspace_binding"
```

`test_routing_invariants.py`:

```text
assertion handler.__module__ != "sandbox.daemon.occ_server"
   -> "sandbox.daemon.services.occ_backend"
obsolete-modules list:
   "sandbox.daemon.occ_handlers"
   "sandbox.daemon.write_edit_handlers"
   "sandbox.daemon.api_handlers"
   ALL still flagged-as-obsolete (none of them exist after the rename either)
```

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox -q
python -c "from sandbox.daemon.rpc import dispatcher; assert dispatcher.OP_TABLE"
python -c "import sandbox.daemon.__main__"
grep -rn "sandbox\.daemon\.layer_stack_server\|sandbox\.daemon\.clients\|sandbox\.daemon\.occ_server\|sandbox\.daemon\.command_exec_server\|sandbox\.daemon\.health_handlers\|sandbox\.daemon\.layer_stack_handlers\|sandbox\.daemon\.server" backend/src backend/tests   # 0
```

### Phase 4 — `control/` → `host/` (flat rename, atomic commit)

File moves:

```text
control/                           ->  host/
control/__init__.py                ->  host/__init__.py
control/daemon/bundle.py           ->  host/daemon/bundle.py    (-> deploy/ in P5)
control/daemon/command.py          ->  host/daemon/command.py   (-> rpc/ in P5)
control/daemon/__init__.py         ->  host/daemon/__init__.py
control/ops/                       ->  host/ops/                 (verbatim)
```

In-tree edits (textual `sandbox.control` → `sandbox.host`):

```text
host/__init__.py                       docstring
host/daemon/__init__.py                docstring
host/daemon/bundle.py                  imports
host/daemon/command.py                 imports + one logger name
host/ops/setup.py                      imports
host/ops/recovery.py                   imports
host/ops/git.py                        imports
host/ops/workspace.py                  imports
host/ops/context.py                    imports
host/ops/__init__.py                   docstring
```

External importer rewrite (~10 files, verified via grep):

```text
sandbox/__init__.py
sandbox/api/facade.py                  (context_preparer_for)
sandbox/api/status/__init__.py         (recovery, setup imports + module-doc)
sandbox/api/tool/_runtime.py           (BUNDLE_REMOTE_DIR, _call_runtime_server)
sandbox/providers/daytona/context.py   (workspace.discover_workspace*,
                                        workspace.prepare_sandbox_runtime_context)
sandbox/providers/protocol.py          (docstring)
plus tests:
backend/tests/.../test_live_setup_api.py
backend/tests/.../test_runtime_bootstrap.py     (heavy: ~15 imports + patches)
backend/tests/.../test_workspace.py             (multiple)
backend/tests/.../test_context.py               (sys.modules patches)
backend/tests/.../test_runtime/test_daemon_transport.py  (uses control.daemon.command)
backend/tests/.../test_runtime/test_bundle.py
backend/tests/.../test_runtime/test_bundle_upload.py     (a second pass after Phase 2)
```

`test_import_fence.py` updates:

```text
SRC_ROOT / "sandbox" / "control"      -> SRC_ROOT / "sandbox" / "host"
"sandbox.control"                      -> "sandbox.host"
test_control_runtime_api_*             -> test_host_daemon_api_*
```

Test directory move:

```text
backend/tests/unit_test/test_sandbox/test_control/  ->  test_host/
```

Note: `control/daemon/install.py` and the `RuntimeCommandClient` class
referenced in the prior draft no longer exist; nothing to migrate or rename
there. `command.py` exposes module-level `_call_runtime_server` /
`_RuntimeDispatchError` / `_RuntimeReadinessError`; those identifiers stay
as-is in this phase (renaming is cosmetic and lives in P5).

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox -q
uv run ruff check backend/src/sandbox backend/tests
grep -rn "sandbox\.control\|sandbox/control" backend/src backend/tests   # 0
python -c "import sandbox.host; sandbox.host"
```

### Phase 5 — Restructure `host/` into `deploy/` + `rpc/` + `ops/`

File moves inside `host/`:

```text
host/daemon/bundle.py        ->  host/deploy/bundle.py
host/daemon/command.py       ->  host/rpc/client.py
host/daemon/__init__.py      ->  delete; replaced by deploy/__init__.py
                                  + rpc/__init__.py
host/ops/                    ->  unchanged
```

In-tree edits:

```text
host/__init__.py                 docstring referenced subpackages
host/deploy/__init__.py          new file
host/deploy/bundle.py             —
host/rpc/__init__.py             new file
host/rpc/client.py                — (no class rename)
host/ops/setup.py                imports of bundle / command updated
host/ops/recovery.py             ditto
host/ops/git.py                  ditto
```

External importer rewrite:

```text
sandbox/api/tool/_runtime.py
   from sandbox.control.daemon.bundle import BUNDLE_REMOTE_DIR
   from sandbox.control.daemon.command import _call_runtime_server
   ->
   from sandbox.host.deploy.bundle import BUNDLE_REMOTE_DIR
   from sandbox.host.rpc.client import _call_runtime_server

backend/tests/.../test_live_setup_api.py            (bundle import path)
backend/tests/.../test_runtime/test_daemon_transport.py  (host.rpc.client)
backend/tests/.../test_runtime/test_bundle.py
backend/tests/.../test_runtime/test_bundle_upload.py     (third pass; this
                                                          test absorbs every
                                                          rename — keep eyes
                                                          on it)
```

Verify:

```text
uv run pytest backend/tests/unit_test/test_sandbox -q
python -c "import sandbox.host.deploy.bundle; import sandbox.host.rpc.client"
grep -rn "sandbox\.host\.daemon\|sandbox/host/daemon" backend/src backend/tests   # 0
```

### Phase 6 — Cleanup pass (single atomic commit)

```text
delete __pycache__ + stray .DS_Store under sandbox/
update non-archived planning docs that reference sandbox.runtime / sandbox.control
update docstrings inside moved modules that still say "runtime" or "control"
   (e.g. logger names, parser progs, error-message prefixes)
   NB: keep "runtime daemon" wording where it still describes behavior;
   only rewrite paths and module names
optional cosmetic: rename sandbox/api/tool/_runtime.py to _daemon_client.py
   (host-side thin client; module name is now misleading) — flag, don't
   force; it requires updating sandbox/api/tool/{edit,read,shell,write}.py
```

Verify:

```text
full uv run pytest backend/tests -q
uv run ruff check backend
one live e2e smoke (shell + write + read round-trip via fresh sandbox)
```

## 4. Risk Mitigations

| Risk | Mitigation |
|---|---|
| Codex parallel session edits `runtime/handlers/*` mid-rename | Run Phases 2–5 only with a clean working tree; coordinate with the codex session to flush its commit first. Phase 1 is safe even with codex running on `runtime/` (only one file moves). |
| `test_import_fence.py` breaks silently because path strings are stale | Update fence test in the same commit as the directory rename of each phase; CI catches drift between commits. |
| `test_bundle_upload.py` carries dense hardcoded `"sandbox/runtime/..."` paths | Treat as its own sub-task in P2 and again in P3 and P5; grep `"sandbox/runtime"` and `"sandbox.runtime"` in the file before claiming done. |
| `test_routing_invariants.py` asserts handler `__module__` against literal strings | Updated alongside the rename in P2 and again in P3. |
| `python -m sandbox.runtime.daemon` launcher persists in already-deployed sandboxes | `_BUNDLE_HASH_MARKER` invalidates on bundle hash change; `ensure_runtime_uploaded` re-uploads on hash mismatch and the next call rewrites the launcher. |
| In-sandbox bundle still has files at old paths after host upgrade | Bundle hash differs after rename → first call after deploy re-extracts the new tree before any handler runs. |
| Test discovery in `test_runtime/` and `test_control/` referenced by configs | Verify `pytest.ini` / `pyproject.toml` / CI workflow files don't hard-code those paths; update in the same rename commit if so. |
| Phase 2 atomic commit is large (~25 importers + bundle + launcher + 3 fence-tests) | Acceptable: blast radius is one rollback. Phase 3 takes the structural risk separately. If P2 churn triggers conflicts on rebase, redo as `git mv` + scripted import rewrite + manual fence/launcher patch. |
| `_RUNTIME_EXCLUDE_PARTS` is a host-side constant whose name is now misleading | Rename to `_DAEMON_EXCLUDE_PARTS` in P2 (single grep across `bundle.py`); test_bundle_upload.py reaches it via attribute monkeypatch on `sandbox.control.daemon.bundle`, not by name string, so safe. |
| `api/tool/_runtime.py` filename becomes a misnomer post-rename | Optional P6 rename; do not force. The host-side thin client name is confusing already; the rename only makes it more so. |

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `sandbox/host/` | Runs in the orchestrator. The "control plane" framing was generic; "host" matches container/VM convention and makes the boundary unambiguous. |
| `sandbox/daemon/` | Runs inside the sandbox as a long-lived process. Matches existing internal terminology ("the runtime daemon"). |
| `host/rpc/client.py` + `daemon/rpc/server.py` | Symmetric RPC vocabulary: client lives on the host, server lives in the daemon. Searchable from either side. |
| `daemon/rpc/dispatcher.py` | The OP_TABLE lookup. The current name `server.py` is wrong: it doesn't serve a socket — `daemon/rpc/server.py` does. |
| `daemon/__main__.py` | `python -m sandbox.daemon` is shorter and idiomatic; replaces `python -m sandbox.runtime.daemon`. |
| `daemon/handlers/` | OP_TABLE entries: parse args, format response, emit timings. One file per verb. |
| `daemon/handlers/_common.py` | Cross-handler scaffolding (`classify_path`, single-path validation, result projection). Pre-existing; not moved or renamed inside this rename. |
| `daemon/services/` | In-process service objects the handlers delegate to (was a confused mix of `*_server.py` and `clients/`). Service modules use role-suffixed names (`*_server`, `*_client`, `*_runner`) so file names disambiguate against `handlers/`. |
| `services/workspace_server.py` (was `layer_stack_server.py`) | Owns `LayerStackWorkspaceServer` + manager cache + `fence_stale_staging`. Suffix kept to disambiguate from `handlers/workspace.py` (the dispatch surface). |
| `services/layer_stack_client.py` (was `clients/layer_stack.py`) | Thin OCC-port adapter around the manager. Suffix kept to disambiguate from `services/workspace_server.py`. |
| `services/occ_backend.py` (was `occ_server.py`) | Owns the `OccBackend` dataclass and `build_occ_backend` factory. Bare `occ.py` would collide with `sandbox.occ`; `_backend` matches the dataclass name. |
| `services/workspace_binding.py` (was `clients/occ.py`) | Owns `RuntimeWorkspaceBindingReader`. Name describes what the module exposes; the `clients/occ.py` name was opaque. |
| `services/shell_runner.py` (was `command_exec_server.py`) | Worker scaffolding for `api.shell` (mount + run + capture + OCC apply). "Runner" matches its role; `_server` was a misnomer (it serves no socket). |
| `host/deploy/` | Bundle build, bundle upload — everything that gets the daemon into a sandbox. |
| `host/rpc/client.py` (was `command.py`) | Names the role (RPC client to the daemon), not the action. Symmetric with `daemon/rpc/server.py`. |
| `sandbox/utils/async_bridge.py` | Used by host AND in-sandbox code; never belonged under `runtime/`. The TODO in `runtime/__init__.py` finally retired. |

## 6. Tests and Exit Criteria

```text
uv run pytest backend/tests/unit_test/test_sandbox -q
uv run pytest backend/tests/unit_test/test_sandbox/test_import_fence.py -q
uv run pytest backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py -q
uv run ruff check backend/src/sandbox backend/tests
grep -rn "sandbox\.runtime\|sandbox\.control" backend/src backend/tests | wc -l   # 0
grep -rn "sandbox/runtime\|sandbox/control" backend/src backend/tests | wc -l     # 0
python -c "import sandbox.host; import sandbox.daemon; import sandbox.utils.async_bridge"
python -c "from sandbox.daemon.rpc.dispatcher import OP_TABLE; assert len(OP_TABLE) >= 13"
python -c "import sandbox.daemon.__main__"
```

Required assertions after Phase 6:

- `sandbox/control/` and `sandbox/runtime/` directories no longer exist
- import-fence test asserts no module imports `sandbox.runtime.*` or
  `sandbox.control.*`
- bundle builder tars `sandbox/daemon/` and the bundle hash differs from
  pre-rename
- `host/rpc/client.py` launches the daemon via `python -m sandbox.daemon`
- daemon's `_load_peer_bootstraps` registers all 13 ops under their new
  handler module paths (`api.edit_file`, `api.read_file`, `api.write_file`,
  `api.shell`, `api.layer_metrics`, `api.runtime.ready`,
  `api.ensure_workspace_base`, `api.build_workspace_base`,
  `api.prepare_workspace_snapshot`, `api.release_workspace_snapshot`,
  `api.layer_stack.fence_stale_staging`, `api.workspace_binding`,
  `overlay.run`)
- a live shell + write + read round-trip succeeds against a freshly
  redeployed bundle
- no production code under `sandbox/daemon/` imports
  `sandbox.providers.daytona.*` (existing fence preserved through the
  rename)
- `sandbox.utils.async_bridge` is the only async-bridge import path
  anywhere in `backend/src` and `backend/tests`
