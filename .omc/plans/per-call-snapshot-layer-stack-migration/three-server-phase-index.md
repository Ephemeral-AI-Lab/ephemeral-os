# Three-Server Workspace Replacement Phase Index

**Status:** draft bundle
**Date:** 2026-05-06
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`

This bundle turns the simplified three-server workspace replacement plan into
implementation-sized phase documents. The phases assume the assigned workspace
is `/testbed`, layer-stack storage is outside that workspace, and guarded
workspace APIs stop treating the real `/testbed` as truth after the workspace
base is built.

## Phase Order

| Phase | Document | Outcome |
|---|---|---|
| 01 | `three-server-phase-01-workspace-binding-base-layer.md` | `layer-stack-server` owns `workspace.json`, builds the `/testbed` base, and serves guarded reads from the active manifest. |
| 01 live E2E | `three-server-phase-01-live-e2e-testing-plan.md` | Live Daytona tests prove `/testbed` base import cost, correctness, concurrency, layer creation, squash/lease behavior, and snapshot assembly over a real imported base repo. |
| 02 | `three-server-phase-02-materialized-lowerdir-cache-leases.md` | Layer-stack can prepare leased, materialized lowerdirs without rebuilding the workspace per shell call. *(Superseded by Phase 04.5; the cache layer is removed.)* |
| 03 | `three-server-phase-03-narrow-client-protocols.md` | OCC and command-exec depend on narrow layer-stack/OCC client protocols, not concrete storage or service internals. |
| 04 | `three-server-phase-04-workspace-replaced-shell.md` | Guarded shell enters `command-exec-server`, replaces `/testbed` with a leased snapshot mount, captures workspace upperdir changes, and keeps the rest of the sandbox filesystem visible. |
| 04.5 | `three-server-phase-04-5-remove-materialized-lowerdir-cache.md` | Phase 04 cache A/B chose `keep_cache_recommendation = false` at every concurrency tier; this phase removes the materialized lowerdir cache and the cache-policy switch, leaving per-lease transient lowerdirs as the only path. |
| 05 | `three-server-phase-05-occ-mutation-gate.md` | `write_file`, `edit_file`, and shell capture converge through `occ.client.OCCClient` and `occ-server` before publishing through layer-stack CAS. |
| 06 | `three-server-phase-06-supervision-transport.md` | The single resident runtime daemon exposes `api.runtime.ready`, fences stale layer-stack staging after restart, and tests OP_TABLE routing against the handler-per-command layout. |
| 07 | `three-server-phase-07-raw-exec-blocking-recovery.md` | Deferred. Do not implement raw-exec workspace blocking or recovery in the current wave; keep public `raw_exec` outside guarded workspace APIs. |
| 08 | `three-server-phase-08-squash-gc-performance.md` | Squash, release-time GC, and no-cache performance gates preserve active leases and bound manifest-depth-sensitive costs. |

## Shared Contract

```text
workspace_root   = /testbed
layer_stack_root = /tmp/eos-sandbox-runtime/layer-stack
```

Routing:

```text
read_file   -> runtime.handlers.read_handler -> OccBackend -> layer-stack snapshot read
write_file  -> runtime.handlers.write_handler -> OCCClient -> OccService -> layer-stack publish
edit_file   -> runtime.handlers.edit_handler -> OCCClient -> OccService -> layer-stack publish
shell       -> runtime.handlers.shell_handler -> command_exec_server
            -> layer-stack snapshot lease -> workspace replacement mount
            -> OCCClient -> OccService -> layer-stack publish
raw_exec    -> provider/runtime escape hatch for setup/status/control/debug;
               Phase 07 blocking/recovery is deferred and not a current gate
status      -> provider/control path; setup uploads runtime, binds workspace base,
               and Phase 06 adds api.runtime.ready as the readiness gate
```

Current dependency rule:

```text
runtime.server
  -> registers OP_TABLE only

runtime.handlers
  -> one host-facing data handler per public verb
  -> read/write/edit share handlers._common for path classification and OccBackend lookup
  -> shell_handler delegates to command_exec_server

runtime.command_exec_server
  -> shell worker pipeline
  -> layer-stack snapshot lease through OccBackend.layer_stack
  -> OCC mutation through OccBackend.occ_client

runtime.occ_server
  -> internal OccBackend factory/cache
  -> owns LayerStackManager, LayerStackClient, SnapshotGitignoreOracle,
     OccService, and OCCClient composition for runtime peers
  -> not registered directly in OP_TABLE

runtime.layer_stack_handlers / runtime.layer_stack_server
  -> workspace binding, base import, snapshot lease, manifest control
  -> no command-exec imports; layer_stack_handlers only reaches occ_server to
     drop the shared backend cache on reset
```

## Cross-Phase Pass Bar

- `read_file`, `write_file`, `edit_file`, and `shell` are host-callable through
  `runtime.handlers/*`.
- `write_file`, `edit_file`, and shell capture reach mutation policy through
  `occ.client.OCCClient`, not a host-callable occ-server API.
- `shell` enters `runtime.handlers.shell_handler` first, then delegates to
  `command_exec_server`.
- Shell capture calls `occ.client.OCCClient.apply_changeset`, not `OccService`
  directly.
- Guarded handlers fail closed when workspace binding or active manifest is
  missing; `runtime.occ_server` itself remains an internal backend factory.
- Guarded workspace routes do not read real `/testbed` as normal workspace
  truth after base build.
- Raw/setup execution blocking and recovery are deferred; do not add them in
  the current wave.
- Active leases survive squash and GC.
