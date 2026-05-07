# Phase 06 Implementation Report - Runtime Supervision and Readiness

**Date:** 2026-05-08
**Plan:** `three-server-phase-06-supervision-transport.md`
**Status:** implemented and unit/static verified

## Summary

Phase 06 keeps the current topology: one resident sandbox runtime daemon and one
`runtime.sock`. It does not restore three resident servers, per-server sockets,
`OCC_OP_TABLE`, or deleted compatibility handler modules.

The implemented contract is:

```text
api.runtime.ready
  -> sandbox.runtime.health_handlers.runtime_ready
  -> private control_plane, data_plane, mutation_gate probes

api.layer_stack.fence_stale_staging
  -> sandbox.runtime.layer_stack_handlers.fence_stale_staging
  -> sandbox.runtime.layer_stack_server.fence_stale_staging

socket missing/refused in control.daemon.command
  -> spawn resident daemon
  -> thin-client api.runtime.ready using the same layer_stack_root
  -> retry original op only when ready=true, except workspace-base bootstrap may
     proceed when the only down probe is the expected missing binding
```

## Files Changed

| File | Change |
|---|---|
| `backend/src/sandbox/runtime/health_handlers.py` | Added the host-callable readiness handler and private control/data/mutation probe helpers. |
| `backend/src/sandbox/runtime/server.py` | Registered `api.runtime.ready` and `api.layer_stack.fence_stale_staging`; tightened handler response typing while preserving dispatch behavior. |
| `backend/src/sandbox/runtime/layer_stack_handlers.py` | Added the host-callable stale-staging fence wrapper. |
| `backend/src/sandbox/runtime/layer_stack_server.py` | Added process-start-aware stale staging cleanup and a once-per-root gate before returning/constructing a `LayerStackManager`. |
| `backend/src/sandbox/control/daemon/command.py` | Added `_RuntimeReadinessError`; daemon relaunch now checks readiness before retrying the original envelope. |
| `backend/src/sandbox/control/ops/setup.py` | Added the post-`api.ensure_workspace_base` readiness assertion requiring `ready=true`, a healthy control plane, and manifest version at least 1. |
| `backend/src/sandbox/control/ops/context.py` | Tightened the provider context-preparer return type so the touched ops mypy slice is green. |
| `backend/tests/unit_test/test_sandbox/test_runtime/test_runtime_ready.py` | Added readiness probe coverage. |
| `backend/tests/unit_test/test_sandbox/test_runtime/test_routing_invariants.py` | Added OP_TABLE routing and deleted-module invariants. |
| `backend/tests/unit_test/test_sandbox/test_runtime/test_stale_staging_fence.py` | Added direct fence and once-per-root manager gate coverage. |
| `backend/tests/unit_test/test_sandbox/test_runtime/test_daemon_transport.py` | Added relaunch-readiness success/failure coverage. |
| `backend/tests/unit_test/test_sandbox/test_runtime_bootstrap.py` | Updated workspace-base setup expectations for the post-base readiness gate. |
| `backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py` | Added `runtime/health_handlers.py` to the runtime bundle required paths. |

## Readiness Semantics

`api.runtime.ready` returns `success=true` for a completed readiness inspection.
`ready` is the binary gate and is true only when all private probes return
`status="ok"`.

Readiness is fail-closed until the assigned workspace is bound. An unbound
layer-stack root makes the control-plane probe `down` and returns `ready=false`.
The daemon relaunch path still calls readiness before retrying
`api.ensure_workspace_base` / `api.build_workspace_base`; those two bootstrap
ops may pass through the missing-binding control-plane probe only when the
data-plane and mutation-gate probes are `ok`.

Setup applies the post-base contract after `api.ensure_workspace_base`:

```text
ready = true
control_plane.status = "ok"
control_plane.details.manifest_version >= 1
```

## Stale Staging Fence

The fence removes stale child directories under:

```text
<layer_stack_root>/staging
```

Only directories with `mtime < daemon_started_at` are removed. Fresh directories
are retained. The fence is called once per process per resolved
`layer_stack_root` before `get_layer_stack_manager(...)` returns either a cached
or newly constructed manager.

This retires the old cross-server crash-isolation idea. After a daemon crash,
the next host call may relaunch the single resident daemon; in-memory manager,
lease, and OCC backend caches are empty; on-disk layer-stack state is the
authority; stale staging from the previous process is fenced before first use.

## Routing Invariants

The tested OP_TABLE entries are:

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
api.runtime.ready               -> sandbox.runtime.health_handlers.runtime_ready
api.layer_stack.fence_stale_staging -> sandbox.runtime.layer_stack_handlers.fence_stale_staging
```

Negative invariants are also tested:

```text
OP_TABLE never routes directly to sandbox.runtime.occ_server.
sandbox.runtime.occ_handlers remains deleted.
sandbox.runtime.write_edit_handlers remains deleted.
sandbox.runtime.api_handlers remains deleted.
```

## Verification

```bash
.venv/bin/pytest \
  backend/tests/unit_test/test_sandbox/test_runtime/test_runtime_ready.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_routing_invariants.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_stale_staging_fence.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_daemon.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_daemon_transport.py \
  backend/tests/unit_test/test_sandbox/test_runtime/test_bundle_upload.py \
  backend/tests/unit_test/test_sandbox/test_runtime_bootstrap.py \
  backend/tests/unit_test/test_sandbox/test_command_exec/test_write_edit_dispatch.py \
  backend/tests/unit_test/test_sandbox/test_occ/test_mutation_gate.py \
  -q
# 83 passed, 1 warning

.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime -q
# 50 passed, 1 warning

.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_command_exec/test_write_edit_dispatch.py -q
# 19 passed, 1 warning

.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ/test_mutation_gate.py -q
# 7 passed, 1 warning

.venv/bin/ruff check \
  backend/src/sandbox/runtime \
  backend/src/sandbox/control/daemon \
  backend/src/sandbox/control/ops \
  backend/tests/unit_test/test_sandbox
# All checks passed

.venv/bin/mypy --config-file backend/mypy.ini \
  backend/src/sandbox/runtime \
  backend/src/sandbox/control/daemon \
  backend/src/sandbox/control/ops
# Success: no issues found in 30 source files
```

The pytest warning is the existing Hypothesis `norecursedirs` collection warning.
No live Daytona E2E gate was run for this phase because the implementation stayed
inside runtime readiness, daemon relaunch, setup assertion, and stale staging
unit seams.
