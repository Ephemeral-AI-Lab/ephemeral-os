# Phase 06 - Runtime Supervision and Readiness

**Status:** implemented and unit/static verified on 2026-05-08
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`
**Predecessor reports:**
- `three-server-phase-04-workspace-replaced-shell-implementation-report.md`
- `three-server-phase-04-5-remove-materialized-lowerdir-cache-implementation-report.md`
- `three-server-phase-05-occ-mutation-gate-implementation-report.md`
- `three-server-phase-05-5-occ-backend-factory-consolidation.md`
- `post-phase-05-handler-per-command-refactor.md`
**Implementation report:** `three-server-phase-06-supervision-transport-implementation-report.md`

## 0. Current Code Shape

The original Phase 06 plan is obsolete. The live runtime no longer has three
resident OS processes, three socket files, `runtime/write_edit_handlers.py`,
`runtime/api_handlers.py`, `runtime/occ_handlers.py`, or `OCC_OP_TABLE`.

The actual topology is:

```text
backend/src/sandbox/runtime/daemon.py
  one resident sandbox process
  one AF_UNIX socket: /tmp/eos-sandbox-runtime/runtime.sock
  imports sandbox.runtime.server and dispatches OP_TABLE in-process

backend/src/sandbox/runtime/server.py
  owns OP_TABLE and _load_peer_bootstraps()
  registers every host-callable runtime op

backend/src/sandbox/runtime/handlers/
  _common.py          shared classifier, single-path validation, OccBackend lookup
  write_handler.py    api.write_file
  edit_handler.py     api.edit_file
  read_handler.py     api.read_file
  shell_handler.py    api.shell entry; delegates to command_exec_server
  metrics_handler.py  api.layer_metrics

backend/src/sandbox/runtime/command_exec_server.py
  shell worker pipeline only:
  prepare snapshot -> workspace replacement mount -> capture -> OCCClient

backend/src/sandbox/runtime/occ_server.py
  internal OccBackend factory/cache:
  LayerStackManager -> LayerStackClient -> SnapshotGitignoreOracle
  -> OccService -> OCCClient

backend/src/sandbox/runtime/layer_stack_handlers.py
  host-callable workspace binding/base/snapshot control ops

backend/src/sandbox/runtime/layer_stack_server.py
  runtime-local LayerStackWorkspaceServer and LayerStackManager cache

backend/src/sandbox/control/daemon/command.py
  host-side provider-backed thin client
  _RUNTIME_THIN_CLIENT_PY sends one envelope to runtime.sock
  _RUNTIME_DAEMON_LAUNCHER starts the resident daemon on socket-missing retry

backend/src/sandbox/control/ops/setup.py
  uploads the runtime bundle, ensures git, then calls api.ensure_workspace_base
  through the guarded runtime path
```

Live `runtime.server.OP_TABLE` registrations are:

```text
api.ensure_workspace_base       -> layer_stack_handlers.ensure_workspace_base
api.build_workspace_base        -> layer_stack_handlers.build_workspace_base
api.prepare_workspace_snapshot  -> layer_stack_handlers.prepare_workspace_snapshot
api.release_workspace_snapshot  -> layer_stack_handlers.release_workspace_snapshot
api.workspace_binding           -> layer_stack_handlers.workspace_binding

api.write_file                  -> handlers.write_handler.write_file
api.edit_file                   -> handlers.edit_handler.edit_file
api.read_file                   -> handlers.read_handler.read_file
api.shell                       -> handlers.shell_handler.shell
api.layer_metrics               -> handlers.metrics_handler.layer_metrics

overlay.run                     -> overlay.handlers.run.handle
```

Pre-implementation negative facts recorded before this phase landed:

```text
api.runtime.ready did not exist yet.
api.layer_stack.fence_stale_staging did not exist yet.
runtime/health_handlers.py did not exist yet.
No readiness-specific runtime error exists yet.
runtime/occ_handlers.py is deleted.
runtime/write_edit_handlers.py is deleted.
runtime/api_handlers.py is deleted.
OCC_OP_TABLE is deleted.
```

So Phase 06 is not "split and supervise three servers." It is:

```text
make the one resident runtime daemon's readiness and restart behavior explicit,
prove OP_TABLE routing against the current handler-per-command layout,
and fence stale on-disk staging left by a daemon crash.
```

## 1. Task Specification

Add a single readiness path for the existing resident daemon. Do not add
per-server sockets, per-server health verbs, client-side route tables, or
compatibility shims for deleted modules.

Implementation scope:

```text
1. Add one host-callable readiness op:

     api.runtime.ready

   It returns success=true plus ready=true/false and three probe records:

     control_plane
       layer_stack_root is usable and active manifest can be read; workspace
       binding may be absent before api.ensure_workspace_base runs

     data_plane
       runtime.handlers._common._services(layer_stack_root) returns an
       OccBackend, command_exec_server._services(...) returns the shell worker
       tuple, and workspace_mount mode detection is available

     mutation_gate
       occ_server.build_occ_backend(layer_stack_root) returns an OccBackend
       with layer_stack, occ_client, gitignore, and manager

2. Wire api.runtime.ready in runtime/server.py:_load_peer_bootstraps.

3. Teach control/daemon/command.py to perform a bounded readiness check after
   the daemon is spawned or relaunched from a socket-missing/refused state.
   This should use the same thin-client transport without recursively calling
   _call_runtime_server.

4. Surface readiness failure as a typed runtime dispatch error. Prefer a name
   that matches the current file, for example _RuntimeReadinessError, and keep
   it beside _RuntimeDispatchError in control/daemon/command.py.

5. Add one staging fence op:

     api.layer_stack.fence_stale_staging

   It removes stale directories under <layer_stack_root>/staging left by a
   previous daemon process. It must not depend on deleted OCC_OP_TABLE or
   occ_handlers surfaces.

6. Call the fence once per process per resolved layer_stack_root before the
   first LayerStackManager is constructed or returned for that root.

7. Add a routing-invariants test pinned to the current OP_TABLE shape.
```

Out of scope:

```text
- three runtime processes
- layer-stack.sock / occ.sock / command-exec.sock
- runtime/supervisor.py
- runtime/thin_client.py
- runtime/server_common.py
- control/daemon/install.py
- control/ops/runtime_services.py
- restoring runtime/occ_handlers.py, runtime/write_edit_handlers.py, or
  runtime/api_handlers.py
- restoring OCC_OP_TABLE
- client-side per-op routing
- raw_exec blocking under /testbed
- squash / GC / performance gates
- removing the copy-backed workspace mount branch
```

## 2. Main Data Objects

```text
RuntimeReadiness
  success      bool
  ready        bool
  probes       list[RuntimeProbe]
  daemon_pid   int
  uptime_s     float
  timings      dict[str, float]

RuntimeProbe
  name         "control_plane" | "data_plane" | "mutation_gate"
  status       "ok" | "down"
  details      dict[str, object]

control_plane details
  workspace_root       str
  manifest_version     int
  manifest_depth       int
  base_root_hash       str

data_plane details
  handlers_services_ready       bool
  shell_services_ready          bool
  workspace_mount_mode          "private_namespace" | "copy_backed"

mutation_gate details
  backend_ready                 bool
  backend_fields                list[str]
  occ_client_class              str

_RuntimeReadinessError
  raised by control/daemon/command.py when readiness fails after daemon spawn
  or relaunch

StaleStagingFenceResult
  success          bool
  staging_root     str
  inspected_dirs   int
  fenced_dirs      int
  fenced_paths     list[str]
  timings          dict[str, float]
```

`RuntimeProbe.status` intentionally avoids a third "degraded" state. The setup
gate needs a binary decision: either guarded runtime APIs are ready, or the
host should fail closed with the failing probe details.

## 3. File And Folder Changes

```text
backend/src/sandbox/runtime/
~-- server.py
    register:
      api.runtime.ready -> health_handlers.runtime_ready
      api.layer_stack.fence_stale_staging -> layer_stack_handlers.fence_stale_staging

+-- health_handlers.py
    runtime_ready(args)
    _probe_control_plane(layer_stack_root)
    _probe_data_plane(layer_stack_root)
    _probe_mutation_gate(layer_stack_root)

~-- layer_stack_handlers.py
    fence_stale_staging(args)

~-- layer_stack_server.py
    call fence_stale_staging once per process per resolved layer_stack_root
    inside get_layer_stack_manager(...) before constructing or returning the
    cached LayerStackManager

backend/src/sandbox/control/daemon/
~-- command.py
    add a readiness call after _runtime_daemon_spawn_command succeeds
    raise _RuntimeReadinessError on non-ready or bad response

backend/src/sandbox/control/ops/
~-- setup.py
    after api.ensure_workspace_base, call api.runtime.ready and require
    ready=true plus control_plane.manifest_version >= 1

backend/tests/unit_test/test_sandbox/test_runtime/
+-- test_runtime_ready.py
+-- test_routing_invariants.py
+-- test_stale_staging_fence.py
~-- test_daemon.py
~-- test_bundle_upload.py
```

Do not add or modify these obsolete files:

```text
backend/src/sandbox/runtime/occ_handlers.py
backend/src/sandbox/runtime/write_edit_handlers.py
backend/src/sandbox/runtime/api_handlers.py
backend/src/sandbox/runtime/supervisor.py
backend/src/sandbox/runtime/thin_client.py
backend/src/sandbox/runtime/server_common.py
backend/src/sandbox/control/daemon/install.py
backend/src/sandbox/control/ops/runtime_services.py
```

## 4. Workflow

### 4.1 Setup And First Runtime Call

Current setup sequence:

```text
sandbox.api.status.create_sandbox(...)
  -> setup_after_create(...)
       start_runtime_bundle_upload(...)
       ensure_git(...)
       finish_runtime_bundle_upload(...)
       run_runtime_bootstrap(...)       # upload-only; does not eagerly spawn daemon
       ensure_workspace_base(...)
         -> call_runtime_api("api.ensure_workspace_base", ...)
            -> _call_runtime_server(...)
               -> thin client tries runtime.sock
               -> socket missing/refused
               -> _RUNTIME_DAEMON_LAUNCHER starts daemon
               -> thin client retries original api.ensure_workspace_base
```

Phase 06 changes the relaunch branch:

```text
socket missing/refused
  -> _RUNTIME_DAEMON_LAUNCHER starts daemon
  -> thin client sends api.runtime.ready with the same layer_stack_root
  -> if ready=false, raise _RuntimeReadinessError with probe details
     except for api.ensure_workspace_base / api.build_workspace_base when the
     only down probe is the expected missing workspace binding
  -> if ready=true, retry the original op
```

Then setup adds a post-base assertion:

```text
ensure_workspace_base(...)
  -> api.ensure_workspace_base
  -> api.runtime.ready
  -> require:
       ready=true
       control_plane.status="ok"
       control_plane.details.manifest_version >= 1
```

### 4.2 Readiness Probes

`api.runtime.ready` is one host-callable op. The individual probes are private
helpers and are not registered in `OP_TABLE`.

```text
runtime_ready(args)
  layer_stack_root = args["layer_stack_root"]

  control_plane:
    require_workspace_binding(layer_stack_root)
    get_layer_stack_manager(layer_stack_root).read_active_manifest()
    return workspace_root, manifest_version, depth, base_root_hash

  data_plane:
    handlers._common._services(layer_stack_root)
    command_exec_server._services({"layer_stack_root": layer_stack_root})
    workspace_mount._private_mount_namespace_available()
    return services readiness and mode:
      true  -> "private_namespace"
      false -> "copy_backed"

  mutation_gate:
    backend = occ_server.build_occ_backend(layer_stack_root)
    assert backend has layer_stack, occ_client, gitignore,
      single_path_gitignore, manager
    return backend_fields and occ_client_class

  ready = all(probe.status == "ok" for probe in probes)
```

This uses Phase 05.5's actual `OccBackend` factory. It must not check
`OCC_OP_TABLE`; that table is intentionally gone.

Important setup distinction: readiness is fail-closed until the assigned
workspace is bound. `api.runtime.ready` returns `ready=false` when
`require_workspace_binding(layer_stack_root)` fails; setup calls readiness after
`api.ensure_workspace_base` and additionally requires `manifest_version >= 1`.
The daemon relaunch path still sends readiness before retrying the workspace-base
op, but it may proceed through this bootstrap-only missing-binding shape so the
base creation call can establish the binding that full readiness requires.

### 4.3 Routing Invariants

The routing test should assert the live table directly:

```text
api.write_file                  -> sandbox.runtime.handlers.write_handler.write_file
api.edit_file                   -> sandbox.runtime.handlers.edit_handler.edit_file
api.read_file                   -> sandbox.runtime.handlers.read_handler.read_file
api.shell                       -> sandbox.runtime.handlers.shell_handler.shell
api.layer_metrics               -> sandbox.runtime.handlers.metrics_handler.layer_metrics

api.ensure_workspace_base       -> sandbox.runtime.layer_stack_handlers.ensure_workspace_base
api.build_workspace_base        -> sandbox.runtime.layer_stack_handlers.build_workspace_base
api.prepare_workspace_snapshot  -> sandbox.runtime.layer_stack_handlers.prepare_workspace_snapshot
api.release_workspace_snapshot  -> sandbox.runtime.layer_stack_handlers.release_workspace_snapshot
api.workspace_binding           -> sandbox.runtime.layer_stack_handlers.workspace_binding

overlay.run                     -> sandbox.overlay.handlers.run.handle

api.runtime.ready                       -> sandbox.runtime.health_handlers.runtime_ready
api.layer_stack.fence_stale_staging     -> sandbox.runtime.layer_stack_handlers.fence_stale_staging
```

Negative assertions:

```text
no OP_TABLE handler module is sandbox.runtime.occ_server
no OP_TABLE handler module contains "occ_handlers"
import sandbox.runtime.occ_handlers raises ModuleNotFoundError
import sandbox.runtime.write_edit_handlers raises ModuleNotFoundError
import sandbox.runtime.api_handlers raises ModuleNotFoundError
```

### 4.4 Daemon Crash And Restart Contract

The retired contract:

```text
kill command-exec-server -> occ/layer-stack remain alive
kill occ-server          -> command-exec/layer-stack remain alive
kill layer-stack-server  -> occ/command-exec remain alive
```

That is false under the current implementation. There is one Python process.
The real contract is:

```text
daemon crash:
  -> active in-flight runtime call fails
  -> next host call may see socket missing/refused
  -> command.py relaunches daemon
  -> command.py checks api.runtime.ready before retrying the original call
  -> in-memory LayerStackManager leases and OccBackend cache are empty
  -> on-disk layer-stack state remains authoritative
  -> stale staging directories are fenced before first use of that root
```

Do not claim cross-server crash isolation in the implementation report.

### 4.5 Stale Staging Fence

The live code has two staging shapes under `<layer_stack_root>/staging`:

```text
L000123-xxxx.staging        LayerPublisher staging before os.replace(...)
occ-commit-...              OCC commit staging used by OccCommitTransaction
```

After a daemon restart, no in-memory lease or stager can validly own those
directories. The fence should remove stale directories under
`<layer_stack_root>/staging` before the first manager use in the new process.

Recommended rule:

```text
fence_stale_staging(layer_stack_root)
  staging_root = layer_stack_root / "staging"
  for each child directory in staging_root:
    if child mtime < daemon process start time:
       shutil.rmtree(child)
```

This is safer than trying to infer "manifest-referenced staging"; manifests
reference immutable layer directories under `layers/`, not staging dirs.

`get_layer_stack_manager(...)` should gate this with a process-local set so
every manager access path, including readiness probes, sees the same cleanup:

```text
_FENCED_STAGING_ROOTS: set[str]

if resolved_layer_stack_root not in _FENCED_STAGING_ROOTS:
    fence_stale_staging({"layer_stack_root": resolved_layer_stack_root})
    _FENCED_STAGING_ROOTS.add(resolved_layer_stack_root)
```

## 5. Tests

Use targeted tests first:

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_runtime_ready.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_routing_invariants.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_stale_staging_fence.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_daemon.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py -q
```

Then run the narrow sandbox runtime suite:

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_command_exec/test_write_edit_dispatch.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ/test_mutation_gate.py -q
```

Final static checks for touched code:

```bash
.venv/bin/ruff check backend/src/sandbox/runtime backend/src/sandbox/control/daemon backend/src/sandbox/control/ops backend/tests/unit_test/test_sandbox
.venv/bin/mypy --config-file backend/mypy.ini backend/src/sandbox/runtime backend/src/sandbox/control/daemon backend/src/sandbox/control/ops
```

Required assertions:

```text
test_runtime_ready.py
  - healthy bound workspace returns ready=true
  - unbound root returns ready=false with control_plane down
  - monkey-patched data-plane failure returns ready=false with data_plane down
  - monkey-patched occ_server.build_occ_backend failure returns ready=false
    with mutation_gate down
  - workspace_mount mode is exactly "private_namespace" or "copy_backed"

test_routing_invariants.py
  - every OP_TABLE row in §4.3 resolves to the expected callable
  - no OP_TABLE callable comes from occ_server
  - deleted legacy modules remain deleted

test_stale_staging_fence.py
  - stale L*.staging dir is removed
  - stale occ-commit-* dir is removed
  - fresh dir newer than the daemon process start time is retained
  - second fence call is idempotent
  - get_layer_stack_manager calls the fence once per process per root

test_daemon.py
  - socket-missing relaunch checks api.runtime.ready before retrying the
    original op
  - readiness failure surfaces _RuntimeReadinessError, not invalid JSON or EOF

test_bundle_upload.py
  - runtime/health_handlers.py is included in the uploaded bundle
```

Live/e2e checks are optional for this phase unless implementation touches setup
semantics broadly. If run, extend the existing setup/live path rather than
adding a parallel live gate:

```text
after sandbox.api.status.create_sandbox completes:
  api.runtime.ready returns ready=true
  control_plane.details.manifest_version >= 1
```

## 6. Step Order

| Step | Change | Verification |
|---|---|---|
| 1 | Add `runtime/health_handlers.py` with private probes against current `handlers`, `occ_server`, and `workspace_mount` interfaces. | `test_runtime_ready.py` imports and unit probe cases pass. |
| 2 | Register `api.runtime.ready` in `runtime/server.py`. | `test_runtime_ready.py` dispatch case passes. |
| 3 | Add `test_routing_invariants.py` for the current OP_TABLE. | Routing test passes and protects later edits. |
| 4 | Add `layer_stack_handlers.fence_stale_staging` and register `api.layer_stack.fence_stale_staging`. | `test_stale_staging_fence.py` direct handler cases pass. |
| 5 | Gate fence once per root in `get_layer_stack_manager`. | Once-per-root test passes. |
| 6 | Update `control/daemon/command.py` relaunch path to call readiness before retry. | New daemon tests pass. |
| 7 | Update `control/ops/setup.py` to assert readiness after workspace base. | Setup unit/live gate passes. |
| 8 | Add `runtime/health_handlers.py` to the bundle required-path test. | Bundle test passes. |
| 9 | Write the Phase 06 implementation report and keep README/index routing text in sync. | Docs no longer mention three sockets as the shipped Phase 06 target. |

## 7. Pass Bar

Phase 06 is complete when:

```text
1. There is still exactly one resident daemon process and one runtime.sock.
2. api.runtime.ready is the only new readiness op.
3. api.layer_stack.fence_stale_staging is the only new cleanup op.
4. OP_TABLE routing is tested against runtime/handlers/*, layer_stack_handlers,
   and overlay.run.
5. No deleted compatibility modules are restored.
6. command.py checks readiness after daemon spawn/relaunch before retrying the
   original call.
7. setup.py verifies readiness after workspace base creation.
8. stale staging dirs from a previous daemon process are removed once per root.
9. The implementation report explicitly retires cross-server crash isolation.
```

## 8. Risks And Decisions

1. **Readiness can accidentally become a mutation.** Keep probes structural and
   read-only. Do not run shell, write files, or apply an empty changeset in
   `api.runtime.ready`.

2. **Workspace mount probing costs a subprocess.** The current
   `_private_mount_namespace_available()` is `@lru_cache(maxsize=1)`, so calling
   it from readiness pays once per daemon process and then returns cached mode.

3. **Staging fence is destructive.** Run it only once per root in a fresh daemon
   process and only remove directories older than the daemon start time. Do not
   run it per request.

4. **The current relaunch path only recognizes socket-missing/refused shapes.**
   Keep Phase 06 scoped to that existing branch. A separate follow-up can decide
   whether bad JSON / EOF / timeout should also trigger relaunch.

5. **The name "three-server" is now historical.** Keep file names stable for
   plan continuity, but use "resident runtime daemon", "handler-per-command",
   "data plane", "control plane", and "mutation gate" in new prose.
