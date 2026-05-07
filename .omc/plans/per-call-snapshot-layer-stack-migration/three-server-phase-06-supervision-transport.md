# Phase 06 - Three-Server Supervision and Transport (Revised for In-Process Drift)

**Status:** draft implementation plan, **revised 2026-05-07** to reflect Phase 01–05 implementation drift
**Source:** `three-server-command-exec-workspace-replacement-simplified.md`
**Predecessor reports:**
- `three-server-phase-04-workspace-replaced-shell-implementation-report.md`
- `three-server-phase-04-5-remove-materialized-lowerdir-cache-implementation-report.md`
- `three-server-phase-05-occ-mutation-gate-implementation-report.md`
**Soft predecessor (recommended-before-06):** `three-server-phase-05-5-occ-backend-factory-consolidation.md`

## 0. What changed since the original plan

The original Phase 06 plan assumed three independent OS processes binding
three sockets (`layer-stack.sock`, `occ.sock`, `command-exec.sock`) plus a
new `supervisor.py` and `thin_client.py`. Phases 04 and 05 deliberately
chose a different shape:

> **Phase 05 report, Interpretation Decision #1:** *occ-server /
> command-exec-server are logical Python modules in the existing runtime
> daemon* — confirmed by Phase 04 for `command_exec_server.py`, then
> followed by Phase 05 for `occ_server.py`.

The runtime today is:

```
backend/src/sandbox/runtime/daemon.py            single resident process
        binds /tmp/eos-sandbox-runtime/runtime.sock          ONE AF_UNIX socket
        imports runtime/server.py                            populates OP_TABLE
        dispatches every api.* verb in-process
backend/src/sandbox/runtime/server.py            in-process op-table dispatcher
backend/src/sandbox/runtime/layer_stack_server.py    logical layer-stack server
backend/src/sandbox/runtime/occ_server.py            logical occ-server (re-exports OCC_OP_TABLE)
backend/src/sandbox/runtime/occ_handlers.py          OCC_OP_TABLE structural surface
backend/src/sandbox/runtime/command_exec_server.py   logical command-exec server (api.shell)
backend/src/sandbox/runtime/write_edit_handlers.py   command-exec handlers for write/edit/read
backend/src/sandbox/runtime/api_handlers.py          layer_metrics + cascade cache drop
backend/src/sandbox/runtime/layer_stack_handlers.py  layer-stack workspace ops + reset cascade

backend/src/sandbox/control/daemon/command.py    thin client that connects to runtime.sock
backend/src/sandbox/control/daemon/bundle.py     deploys the runtime bundle
```

Concrete drift items the original plan got wrong:

| Original Phase 06 assumption | Actual Phase 01–05 outcome |
|---|---|
| Three OS processes, three `.sock` files | One process (`runtime.daemon`), one socket (`runtime.sock`). `*_server.py` are logical modules. |
| New `runtime/supervisor.py` | Daemon launch + readiness already lives in `control/daemon/command.py` (`_DAEMON_LAUNCH_SCRIPT`, `_looks_like_socket_missing`, automatic relaunch + retry). |
| New `runtime/thin_client.py` routes by op prefix | One inline thin client (`_RUNTIME_CLIENT_PY` in `control/daemon/command.py`); routing is the in-process `OP_TABLE` lookup in `runtime/server.py`. |
| New `runtime/server_common.py` | Common helpers ended up in `runtime/handlers/_common.py` (Phase 04.5/05 layout). No `server_common.py` is needed. |
| New `control/daemon/install.py`, `control/ops/runtime_services.py` | Neither file landed. `control/daemon/{command,bundle}.py` and `control/ops/{setup,context,git,recovery,workspace}.py` cover the actual scope. |
| "Remove fork fallback" after guarded soak | There is no fork fallback. `command_exec/workspace_mount.py` chooses between `_run_private_mount_namespace` (`unshare -Urm`) and `_run_copy_backed_mount`. The copy-backed branch writes to a per-call `run_dir/workspace` (not real `/testbed`) and is **not** a real-`/testbed` fallthrough. The original §6 risk this bullet hedged against — "writable real `/testbed` fallback enabled" — does not exist in current code. |
| Crash isolation between logical servers (kill command-exec, occ/layer-stack remain) | **Structurally impossible** in a single process. The original promise must be retired explicitly, not silently rephrased. |
| `OCC_OP_TABLE = {apply_changeset, start, stop, health}` is registered with the dispatcher | OCC_OP_TABLE is a **structural assertion target** only. `start/stop/health` are defined on `occ_handlers.py` but never registered against `runtime/server.py`. Same for `layer_stack` and `command_exec` — there are no per-server health verbs today. |
| Setup builds workspace base after starting all three servers | Setup runs `layer_stack_handlers.build_workspace_base` through the same in-process daemon; "all three servers ready" reduces to "daemon is up and the three logical-module imports succeeded" — already true at first request. |
| **Per-verb routing**: `read_file → layer-stack-server`, `write/edit → occ-server`, `shell → command-exec-server` | **Obsolete since Phase 05.** Phase 05 §6 made command-exec-server the **single host-facing data API surface**: `api.read_file`, `api.write_file`, `api.edit_file`, and `api.shell` **all** dispatch to command-exec-server modules (`runtime/write_edit_handlers.{read,write,edit}_file` and `runtime/command_exec_server.shell`). occ-server is fully internal — there are zero host-callable `api.write_*` / `api.edit_*` / `api.read_*` ops on it; mutations reach occ-server only through the in-process `OCCClient.apply_changeset` boundary that command-exec-server consumes. layer-stack-server is host-callable only for control-plane ops (`api.workspace_binding`, `api.{build,ensure}_workspace_base`, `api.{prepare,release}_workspace_snapshot`) plus the diagnostic `api.layer_metrics`. |

Implications for Phase 06:

- The "route different verbs to different servers" framing is dead. The
  routing-invariants test (§3, §4.2) asserts the **post-Phase-05 shape**
  (read/write/edit/shell all → command-exec-server handlers; only
  control-plane ops on layer-stack-server; nothing host-callable on
  occ-server beyond a possible health probe).
- "Per-server health" still has value, but the asymmetry matters: only
  command-exec-server has a host-facing data surface, so its health
  signal is the one users care about most. layer-stack-server health is
  about the control plane (workspace binding + active manifest); occ-
  server has no host-callable surface at all and the most useful
  "health" check on it is a one-shot probe that the in-process
  `OCCClient` import succeeds and `OCC_OP_TABLE` is intact.
- "Crash isolation between logical servers" was already retired by
  the in-process design; the per-verb routing collapse compounds the
  point — there is genuinely **one host-facing server** (command-exec)
  plus a control-plane server (layer-stack) plus an internal mutation
  gate (occ). Per-server crash isolation would require splitting at
  least command-exec out, and is firmly out of scope.

This phase ratifies that shape and finishes the supervision / transport
/ health story for the **command-exec-as-single-data-server** topology.

## 1. Task Specification

Make daemon supervision, routing invariants, and restart fencing
explicit and testable under the **command-exec-as-single-data-server**
topology that Phase 05 shipped. Surface a single readiness probe that
host setup can gate on, and define daemon-restart fencing for the only
piece of state that survives across daemon restarts (on-disk staging
directories under `<layer_stack_root>/staging`).

Implementation scope:

```text
1. add ONE host-facing readiness verb:
     api.runtime.ready
   It composes three internal probes into one response:
     - control_plane: layer-stack-server can read workspace.json + active manifest
     - data_plane:    command-exec-server's _services(...) builds without raising
                      and (best-effort) reports the workspace_mount mode
                      (private namespace vs copy-backed)
     - mutation_gate: OCC_OP_TABLE is intact and OCCClient.apply_changeset is
                      importable in-process (occ-server has no host-facing
                      surface to probe directly)
   This replaces the originally-planned three per-server health verbs.
   Two of the three "servers" have no host-facing data surface, so a
   single composed verb is the right granularity.
2. wire api.runtime.ready in runtime/server.py:_load_peer_bootstraps;
   no other new public ops in this phase.
3. promote the existing daemon launch + retry path
   (control/daemon/command.py) to call api.runtime.ready after relaunch
   and surface a structured ServerReadinessError on failure (replaces the
   current best-effort socket-missing detection).
4. add a daemon-restart fencing handler:
     api.layer_stack.fence_stale_staging
   that scans <layer_stack_root>/staging for half-published directories
   and cleans them; LayerStackWorkspaceServer's first instantiation per
   process per layer_stack_root calls it once (idempotent).
5. assert routing invariants in unit tests, pinned to the post-Phase-05
   shape:
     api.read_file                   -> write_edit_handlers.read_file       (command-exec)
     api.write_file                  -> write_edit_handlers.write_file      (command-exec)
     api.edit_file                   -> write_edit_handlers.edit_file       (command-exec)
     api.shell                       -> command_exec_server.shell           (command-exec)
     api.workspace_binding           -> layer_stack_handlers.workspace_binding   (layer-stack control plane)
     api.build_workspace_base        -> layer_stack_handlers.build_workspace_base
     api.ensure_workspace_base       -> layer_stack_handlers.ensure_workspace_base
     api.prepare_workspace_snapshot  -> layer_stack_handlers.prepare_workspace_snapshot
     api.release_workspace_snapshot  -> layer_stack_handlers.release_workspace_snapshot
     api.layer_metrics               -> api_handlers.layer_metrics          (diagnostic)
     api.runtime.ready               -> health_handlers.runtime_ready       (new)
     api.layer_stack.fence_stale_staging -> layer_stack_handlers.fence_stale_staging (new)
   Negative invariant: NO `api.write_*` / `api.edit_*` / `api.read_*`
   handler is registered against occ_server (occ-server is internal; the
   only path to OCC is OCCClient.apply_changeset in-process).
6. document and explicitly retire the original cross-server crash
   isolation contract; replace it with the in-process crash-then-restart
   contract (see §4.3).
```

Out of scope:

```text
- separating layer-stack-server / occ-server / command-exec-server into
  distinct OS processes with their own sockets (deferred indefinitely;
  re-open only if a future failure mode demands cross-server crash isolation)
- raw_exec blocking under /testbed (Phase 07)
- squash / GC / cache / performance gates (Phase 08)
- removing the copy-backed workspace_mount branch (it is not a fork
  fallback and does not write real /testbed; reconsider only if it shows
  measurable correctness or perf drift)
- changing OCCClient or LayerStackClient protocol surfaces (Phase 03 already settled them)
- a separate live-e2e gate file: extend the existing setup live-e2e to
  assert api.runtime.ready returns true post-setup, instead of adding a
  parallel test
```

Exit condition:

```text
1. setup runs daemon -> calls api.runtime.ready -> all three internal
   probes pass (control_plane / data_plane / mutation_gate) -> guarded
   API is declared ready
2. every public guarded verb dispatches to the expected handler module
   under the post-Phase-05 topology (read/write/edit/shell -> command-
   exec; workspace_* -> layer-stack; nothing host-callable on occ-server),
   asserted by a unit test
3. on daemon crash, control/daemon/command.py relaunches and re-runs
   api.runtime.ready before retrying any host call; failures surface as
   a typed ServerReadinessError, never as a stale socket EOF
4. on daemon restart, stale on-disk <layer_stack_root>/staging dirs are
   fenced before the first guarded mutation; in-memory state
   (LeaseRegistry, OccService merger queue, _SERVICE_CACHE) is reset for
   free because the process is new
5. cross-server crash isolation is explicitly out of scope and called
   out in this plan, this phase's report, and the index README
```

## 2. Main Data Objects

```text
RuntimeEnvelope
  op           str                     # "api.runtime.ready", "api.layer_stack.fence_stale_staging", ...
  args         Mapping[str, object]
  request_id   str (optional)
  actor_id     str (optional)

ProbeResult
  name         Literal["control_plane", "data_plane", "mutation_gate"]
  status       Literal["ok", "degraded", "down"]
  details      Mapping[str, object]    # control_plane: manifest_version, workspace_bound, base_root_hash
                                       # data_plane:    workspace_mount_mode ("namespace"|"copy_backed"),
                                       #                services_cache_ready (bool)
                                       # mutation_gate: occ_op_table_keys (sorted list),
                                       #                occ_client_importable (bool)

RuntimeReadiness
  ready        bool                    # AND of the three probes' status == "ok"
  probes       list[ProbeResult]
  daemon_pid   int
  socket_path  str
  uptime_s     float

ServerReadinessError(Exception)
  raised by control/daemon/command.py when api.runtime.ready returns
  ready=false or fails the bounded retry budget

StaleStagingFenceResult
  staging_root        str
  inspected_dirs      int
  fenced_dirs         int
  fenced_paths        list[str]
  timings             dict[str, float]
```

Rationale: the original draft of this plan modeled the response as
`servers: list[ServerHealth]` with one entry per logical server. Under
the post-Phase-05 topology, **only command-exec-server has a host-facing
data surface**, so "per-server health" doesn't carve at a real joint.
The probes (`control_plane` / `data_plane` / `mutation_gate`) name what
breaks on failure, not which logical Python module owns the check.

## 3. File/Folder Structure Change

Target additions and updates (`+` new, `~` modified):

```text
backend/src/sandbox/runtime/
~-- server.py
       _load_peer_bootstraps registers exactly two new ops:
         api.runtime.ready
         api.layer_stack.fence_stale_staging
       (no per-server health verbs; the three probes live INSIDE
        runtime_ready and are not separately host-callable)
+-- health_handlers.py
       runtime_ready(args)              # composes the three probes
       _probe_control_plane(args)
       _probe_data_plane(args)
       _probe_mutation_gate(args)
       (private probe helpers; not registered as host-callable ops)
~-- occ_handlers.py
       (no change in this phase; OCC_OP_TABLE.health remains a
        structural assertion target. _probe_mutation_gate reads
        OCC_OP_TABLE for its check.)
~-- layer_stack_handlers.py
       fence_stale_staging(args)  # scans <layer_stack_root>/staging,
                                  # cleans dirs that lack a manifest entry
~-- layer_stack_server.py
       LayerStackWorkspaceServer.__init__ calls fence_stale_staging
       once per (process, layer_stack_root); module-level flag gates
       the call so the cost is paid at most once per restart, never
       per-request

backend/src/sandbox/control/daemon/
~-- command.py
       after _ensure_daemon_running: call api.runtime.ready;
       on non-ready or timeout, raise ServerReadinessError;
       on success, proceed with the original send

backend/src/sandbox/control/ops/
~-- setup.py
       after build_workspace_base, call api.runtime.ready and assert
       ready=true before declaring sandbox setup complete

backend/tests/unit_test/test_sandbox/test_runtime/
+-- test_routing_invariants.py        # asserts OP_TABLE wires each
                                      # public op to the expected handler
                                      # module/function under the post-
                                      # Phase-05 topology + negative
                                      # invariants (no api.write_*/edit_*/
                                      # read_* on occ_server)
+-- test_runtime_ready.py             # api.runtime.ready returns ready=true
                                      # with three "ok" probes on a healthy
                                      # sandbox; ready=false with the
                                      # failing probe's name in details
                                      # when each probe is monkey-patched
                                      # to fail
+-- test_stale_staging_fence.py       # fence_stale_staging cleans an
                                      # orphan staging dir, leaves a
                                      # manifest-referenced one alone
~-- test_daemon.py                    # add a "missing socket -> relaunch
                                      # -> ready=true -> request succeeds"
                                      # case; existing service-cache
                                      # tests untouched
~-- test_bundle_upload.py             # required-paths list extended
                                      # with health_handlers.py
```

Files **not** added (originally in Phase 06 plan, now obsolete):

```text
backend/src/sandbox/runtime/supervisor.py        not needed
backend/src/sandbox/runtime/thin_client.py       not needed
backend/src/sandbox/runtime/server_common.py     not needed
backend/src/sandbox/control/daemon/install.py    not needed
backend/src/sandbox/control/ops/runtime_services.py  not needed
```

## 4. Workflow Demonstration

### 4.1 Setup → daemon launch → readiness probe

```text
sandbox.api.status.create_sandbox(project_dir="/testbed")
  -> provider.create(...)
  -> setup_after_create(...)
  -> control.daemon.command.ensure_runtime_daemon_started(...)
       nohup python -m sandbox.runtime.daemon --socket runtime.sock --pid-file runtime.pid &
       wait until socket appears, then send api.runtime.ready
  -> daemon imports runtime.server.py
       _load_peer_bootstraps registers all api.* ops, plus the two new
       Phase 06 ops: api.runtime.ready, api.layer_stack.fence_stale_staging
  -> control.daemon.command sends:
       {"op": "api.runtime.ready", "args": {"layer_stack_root": ".../layer-stack"}}
  -> daemon dispatches to health_handlers.runtime_ready, which runs
     three INTERNAL probes in sequence (none are separately host-callable):
       _probe_control_plane(layer_stack_root)
         -> read workspace.json + active manifest
         -> {status: "ok", details: {workspace_bound: true|false,
             manifest_version: int, base_root_hash: str}}
       _probe_data_plane(layer_stack_root)
         -> command_exec_server._services(...) builds without raising
         -> workspace_mount mode is detected (cached one-shot)
         -> {status: "ok", details: {services_cache_ready: true,
             workspace_mount_mode: "namespace"|"copy_backed"}}
       _probe_mutation_gate()
         -> from sandbox.runtime.occ_server import OCC_OP_TABLE
         -> set(OCC_OP_TABLE) == {"apply_changeset","start","stop","health"}
         -> sandbox.occ.client.OCCClient is importable
         -> {status: "ok", details: {occ_op_table_keys: [...],
             occ_client_importable: true}}
  -> response = {ready: true, probes: [...], daemon_pid, uptime_s}
  -> setup_after_create proceeds to layer_stack_handlers.build_workspace_base
  -> after base build, setup re-checks api.runtime.ready and asserts
     control_plane.details.manifest_version >= 1
  -> guarded API is now ready
```

### 4.2 Public verb routing (in-process OP_TABLE)

The post-Phase-05 topology has **command-exec-server as the single host-
facing data API surface**. Existing ops:

```text
# Data plane — all on command-exec-server
api.read_file        -> write_edit_handlers.read_file       (command-exec)
api.write_file       -> write_edit_handlers.write_file      (command-exec)
api.edit_file        -> write_edit_handlers.edit_file       (command-exec)
api.shell            -> command_exec_server.shell           (command-exec)

# Control plane — on layer-stack-server
api.workspace_binding             -> layer_stack_handlers.workspace_binding
api.build_workspace_base          -> layer_stack_handlers.build_workspace_base
api.ensure_workspace_base         -> layer_stack_handlers.ensure_workspace_base
api.prepare_workspace_snapshot    -> layer_stack_handlers.prepare_workspace_snapshot
api.release_workspace_snapshot    -> layer_stack_handlers.release_workspace_snapshot

# Diagnostic
api.layer_metrics    -> api_handlers.layer_metrics

# Other
overlay.run          -> overlay.handlers.run.handle
```

Phase 06 adds exactly two new ops:

```text
api.runtime.ready                       -> health_handlers.runtime_ready
api.layer_stack.fence_stale_staging     -> layer_stack_handlers.fence_stale_staging
```

Negative invariant (asserted by the routing test): no `api.write_*` /
`api.edit_*` / `api.read_*` op is registered against `occ_server` or
`occ_handlers` — occ-server is internal and is reached only through the
in-process `OCCClient.apply_changeset` boundary.

The thin client (`control/daemon/command.py:_RUNTIME_CLIENT_PY`) does **not**
do per-op routing. There is one socket, one OP_TABLE, one dispatcher.
Routing assertions belong in unit tests against `OP_TABLE`, not in a
client-side route table.

### 4.3 Crash and restart contract (replaces original §1 crash bullets)

The original plan said:

```text
kill command-exec-server
  -> active shell calls fail
  -> occ/layer-stack remain intact

kill occ-server
  -> mutations fail closed
  -> layer-stack manifest remains valid

kill layer-stack-server
  -> reads/mutations fail closed
  -> restart reloads workspace binding and fences unresolved leases/staging
```

Under the in-process design **none of these are achievable**: the three
servers share a Python interpreter; killing one kills all three. The
honest contract is:

```text
daemon crash (SIGKILL, OOM, unhandled exception):
  -> client thin-call sees socket EOF / ECONNREFUSED
  -> control/daemon/command.py relaunches the daemon and re-runs
     api.runtime.ready
  -> active write/edit/shell that were mid-flight are lost; the host
     caller observes a transport error and retries (idempotency is the
     caller's responsibility)
  -> on-disk layer-stack CAS state is intact: workspace.json, the
     active manifest, and committed layers are not touched by an
     in-process crash; only in-memory LeaseRegistry / OccService merger
     queue / _SERVICE_CACHE are dropped
  -> on restart, layer_stack_server.LayerStackWorkspaceServer's first
     instantiation per layer_stack_root fences stale staging via
     api.layer_stack.fence_stale_staging
  -> first post-restart guarded mutation publishes against the active
     manifest as if it were the first mutation of a fresh sandbox
```

What is **explicitly retired**:

```text
"server crashes fail closed without corrupting layer-stack" remains true
  (on-disk CAS is intact)

"crashes are scoped to one logical server" is FALSE under the in-process
  model and is removed from the contract; revisit if a future failure
  mode demands per-server isolation
```

### 4.4 Stale staging fence

```text
daemon dies mid-publish:
  <layer_stack_root>/staging/<staging_id>/   contains staged blobs but
                                             never published a layer
  <layer_stack_root>/manifests/active.json   still points at version N
                                             (not N+1)
  <layer_stack_root>/layers/                 has no L<N+1> for this staging_id

post-restart:
  api.layer_stack.fence_stale_staging({"layer_stack_root": ...})
    -> read active manifest N
    -> for each <layer_stack_root>/staging/<staging_id>/:
         if no layer in <layer_stack_root>/layers/ references this staging_id
            and no in-memory lease pins it (always true post-restart)
         -> shutil.rmtree(staging dir)
         -> append to fenced_paths
    -> return StaleStagingFenceResult
```

`fence_stale_staging` is idempotent and safe to call on a clean restart
(fenced_dirs == 0). It is **not** called on every request — only at
LayerStackWorkspaceServer's first instantiation per process, gated by a
module-level flag, so it has zero per-call cost.

## 5. Naming Conventions and Rationale

| Name | Rationale |
|---|---|
| `api.runtime.ready` | The one host-facing readiness verb. Composes three internal probes; matches `_load_peer_bootstraps`'s flat `api.*` namespace. No per-server `*.health` verbs because under the post-Phase-05 topology only command-exec has a host-facing data surface — per-server health doesn't carve at a real joint. |
| `_probe_{control_plane,data_plane,mutation_gate}` | Probe names describe **what breaks on failure**, not which Python module owns the check. `control_plane` = workspace binding + active manifest readable; `data_plane` = command-exec services build; `mutation_gate` = OCC surface intact. |
| `health_handlers.py` | New module hosts `runtime_ready` plus the three private `_probe_*` helpers. Probes are **not** registered against `runtime/server.py` — the only host-callable health verb is `api.runtime.ready`. |
| `ServerReadinessError` | Distinct from `WorkspaceBindingError` and `ManifestConflictError`; raised exclusively by the thin client when readiness probe fails after relaunch retries. |
| `fence_stale_staging` | "Fence" reuses the language already used in the original Phase 06 §1 ("fence unsafe leases and staging"); "stale_staging" disambiguates from the no-op lease fencing (in-memory and free at restart). |
| no per-server `.sock` files | One process, one socket, one dispatcher. Phase 04 and Phase 05 ratified this; revisiting would invalidate ~80% of `runtime/`. |

## 6. Tests and Exit Criteria

```text
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_routing_invariants.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_runtime_ready.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_stale_staging_fence.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_runtime/test_daemon.py -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
.venv/bin/ruff check backend/src/sandbox backend/tests/unit_test/test_sandbox
.venv/bin/mypy --config-file backend/mypy.ini backend/src/sandbox/runtime/health_handlers.py backend/src/sandbox/runtime/server.py backend/src/sandbox/runtime/layer_stack_handlers.py backend/src/sandbox/control/daemon/command.py
```

Required assertions (unit):

```text
Routing invariants (test_routing_invariants.py):
- runtime.server.OP_TABLE wires every §4.2 op to the expected handler
  function (one assertion per row); unknown ops must not appear
- POSITIVE: api.{read,write,edit}_file each resolve to a callable in
  runtime.write_edit_handlers
- POSITIVE: api.shell resolves to runtime.command_exec_server.shell
- POSITIVE: api.workspace_* and api.{build,ensure}_workspace_base
  resolve to runtime.layer_stack_handlers.*
- NEGATIVE: no api.write_* / api.edit_* / api.read_* op resolves to
  any callable defined in runtime.occ_server or runtime.occ_handlers
- NEGATIVE: occ-server's only host-facing surface in OP_TABLE is the
  Phase 06-introduced absence — i.e., set(OP_TABLE.keys()) ∩
  {names defined in occ_server / occ_handlers} == ∅

Readiness probe (test_runtime_ready.py):
- api.runtime.ready returns ready=true with three "ok" probes on a
  freshly-built sandbox; probe names are exactly
  ["control_plane", "data_plane", "mutation_gate"]
- api.runtime.ready returns ready=false when any one probe is monkey-
  patched to raise; the failing probe's name appears in
  response["probes"][i]["name"] with status="down" and the original
  exception captured in details
- _probe_control_plane reports manifest_version=0 (unbound) before
  build_workspace_base and >=1 after; workspace_bound flips accordingly
- _probe_data_plane reports workspace_mount_mode in
  {"namespace","copy_backed"} matching what
  workspace_mount._private_mount_namespace_available() returns; the
  probe is cached so calling api.runtime.ready a second time does not
  re-invoke unshare
- _probe_mutation_gate reports occ_op_table_keys equal to
  ["apply_changeset","health","start","stop"] (sorted) and
  occ_client_importable=true

Stale staging fence (test_stale_staging_fence.py):
- api.layer_stack.fence_stale_staging removes an orphan
  <layer_stack_root>/staging/<id>/ that the manifest does not reference
- it leaves a staging dir referenced by the active manifest untouched
  (if any current flow leaves one — otherwise this branch is asserted
  via a synthetic referenced staging dir)
- it is idempotent: a second call returns fenced_dirs=0
- LayerStackWorkspaceServer construction calls it once per (process,
  layer_stack_root); a second LayerStackWorkspaceServer for the same
  root in the same process does not re-fence

Daemon relaunch (test_daemon.py, additive cases):
- control.daemon.command raises ServerReadinessError when api.runtime.ready
  returns ready=false on relaunch
- after daemon relaunch (simulated by deleting the socket and PID files),
  the next thin-client call succeeds end-to-end and api.runtime.ready
  reports ready=true

Phase 05 invariants preserved:
- OCC_OP_TABLE structural surface is unchanged
  ({apply_changeset, start, stop, health}); test_mutation_gate.py keeps
  passing because Phase 06 does NOT register any of those four under
  api.occ.* in OP_TABLE — they remain a module-surface assertion target,
  read in-process by _probe_mutation_gate
```

Required assertions (live, deferred to setup live-e2e gate):

```text
- after sandbox.api.status.create_sandbox completes, api.runtime.ready
  returns ready=true and manifest_version >= 1
- after a forced daemon kill mid-shell, the next sandbox.api.tool.shell
  succeeds; on-disk active manifest version did not regress
- after a forced daemon kill mid-publish (i.e., between staging blobs
  written and CAS publish), restart fences the orphan staging dir and
  the next write_file publishes against the original active manifest
```

Cross-cutting exit criteria:

```text
- runtime.daemon is the only resident process; no supervisor.py /
  thin_client.py / server_common.py module exists
- routing-invariant unit test is the single source of truth for the §1
  routing rule
- copy-backed workspace_mount branch is unchanged (it is safe; not a
  real-/testbed fallback) — no removal in this phase
- Phase 06 implementation report explicitly retires the original
  cross-server crash isolation contract and links to §4.3 here
- bundle test_bundle_upload required-paths list includes
  runtime/health_handlers.py
```

## 7. Sequencing relative to Phase 05.5

Phase 05.5 (`three-server-phase-05-5-occ-backend-factory-consolidation.md`)
collapses the triplicated `(LayerStackClient, OCCClient,
SnapshotGitignoreOracle, LayerStackManager)` cache plumbing into one
factory in `runtime/occ_server.py`. Phase 06 does **not** depend on it,
but the implementer should choose one of two sequencing paths:

| Path | Effect on Phase 06 |
|---|---|
| **05.5 lands first (recommended)** | `test_stale_staging_fence.py` and `test_daemon.py` cache invalidation use `occ_server.drop_backend_cache(...)` (one call). `health_handlers.layer_stack_health` reads `occ_server.build_occ_backend(root).manager` to surface lease/manifest state. Less code in `health_handlers.py`. |
| **06 lands first** | The same cache cascade as today: `api_handlers.drop_services_cache` → `command_exec_server` + `write_edit_handlers` + `_common`. `health_handlers.layer_stack_health` reads `runtime.layer_stack_server.get_layer_stack_manager(root)` directly. After 05.5 lands, a small follow-up cleans this up. |

Default to **05.5 lands first** unless a parallel agent has already
started Phase 06 — in which case finish 06 with the cascade form, then
the cleanup on top of 05.5 is one diff. Either ordering keeps every
intermediate commit green.

## 8. Risks and Open Questions

### Risks

1. **Stale staging fence is destructive.** A bug that misclassifies a
   live staging dir as orphan would delete in-progress work. Mitigation:
   only run the fence at first-instantiation-per-process of
   LayerStackWorkspaceServer (so only after a daemon restart, when no
   in-memory lease exists by definition); compare `<staging_id>` against
   layer-side references; cap the scan to dirs older than a sentinel
   (e.g., older than the daemon's own start time).

2. **Crash isolation contract removal might be controversial later.**
   Future work may want per-server crash isolation back. Mitigation:
   §4.3 states this explicitly so the decision is reversible; a future
   phase can split command-exec out into a separate process (it is the
   only host-facing data server, so isolating it gives the most leverage)
   and the original Phase 06 plan can be revived in shape.

3. **Parallel codex sessions (per memory note).** Five-plus runtime files
   and the control daemon are touched. Mitigation: stage with explicit
   file paths only (never `git add <dir>`); verify HEAD before declaring
   done; bundle each step into one atomic commit.

4. **`_probe_mutation_gate` is purely structural.** It checks
   `set(OCC_OP_TABLE.keys())` and `OCCClient` importability — both are
   import-time invariants. If a future regression breaks OCC at the
   runtime/serial-merger layer (not the surface), the probe will still
   report "ok" while real mutations fail. Acceptable for Phase 06
   (the data_plane probe transitively exercises OCC by calling
   `command_exec_server._services` which builds an `OCCClient`); a
   functional probe that exercises an idempotent no-op
   `apply_changeset(empty_changeset)` is deferred to a future phase
   only if mutation regressions slip past the structural check.

### Open questions

1. Should `runtime_ready`'s probe fan-out be parallel (asyncio.gather)
   or sequential? All three probes are cheap and read-only; sequential
   is simpler. Default: sequential. Reconsider only if any single probe
   exceeds 5 ms.
2. Should `_probe_data_plane` actually invoke
   `_private_mount_namespace_available()` (which runs `unshare -Urm true`)?
   That probe costs one subprocess per call. Default: probe once per
   process and cache (matches the existing one-shot detection inside
   `workspace_mount.py`); surface the cached value as
   `details.workspace_mount_mode`. The first `runtime_ready` call after
   daemon launch pays the probe; subsequent calls hit the cache.
3. Should `fence_stale_staging` also fence `<run_dir>` orphans under
   `/dev/shm/eos-command-exec/...`? Phase 04 puts per-shell upper/work
   dirs there and they leak on daemon crash. Default: **yes** — extend
   the fence to that directory tree, gated on first
   `LayerStackWorkspaceServer` instantiation per process, with a TTL of
   "older than daemon start time".
4. Does `api.runtime.ready` need to be cacheable on the host side? A
   cache hides the second relaunch's first-call latency. Default: no —
   the current relaunch path already pays this once per sandbox lifetime;
   no per-call cost. Reconsider if relaunch becomes hot.
4. Does `api.runtime.ready` need to be cacheable on the host side? A
   cache hides the second relaunch's first-call latency. Default: no —
   the current relaunch path already pays this once per sandbox lifetime;
   no per-call cost. Reconsider if relaunch becomes hot.

## 9. Migration and Rollback

### Migration

No host-side migration. `sandbox.api.tool.{write,edit,read,shell}.py`
host wrappers do not change. The two new verbs (`api.runtime.ready`,
`api.layer_stack.fence_stale_staging`) are additive.

### Rollback

Pure code rollback: `git revert` the Phase 06 commits restores Phase
05.5 (or Phase 05) state. No durable data shape changes; no manifest /
lease / staging schema changes.

## 10. Step Order and Verification

Each step ends with `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q`
all-green. Each step is one atomic commit so a mid-phase abort leaves a
clean tree.

| Step | Change | Verification |
|---|---|---|
| 1 | Add `runtime/health_handlers.py` with `runtime_ready` + private `_probe_control_plane`, `_probe_data_plane`, `_probe_mutation_gate`. No dispatcher wiring yet. | `.venv/bin/pytest test_runtime -q` passes; new module imports cleanly. |
| 2 | Wire `api.runtime.ready` in `runtime/server.py:_load_peer_bootstraps` (one new op). Add `test_runtime_ready.py`. | New test passes; existing `test_daemon` and bundle tests pass. |
| 3 | Add `test_routing_invariants.py` covering the full §4.2 table (positive: every public op resolves to the expected handler; negative: no `api.{write,edit,read}_*` resolves to occ_server / occ_handlers). | Test passes; protects routing for the rest of the phase. |
| 4 | Add `layer_stack_handlers.fence_stale_staging` + `api.layer_stack.fence_stale_staging` op + `test_stale_staging_fence.py`. | New test passes; fence is idempotent under repeat call. |
| 5 | Wire `LayerStackWorkspaceServer.__init__` to call `fence_stale_staging` once per (process, layer_stack_root) via a module-level flag. Optionally extend the fence to `<run_dir>` orphans under `/dev/shm/eos-command-exec/...` (open question 3). | `test_stale_staging_fence.py` adds an integration case; no regression in `test_workspace_binding.py`. |
| 6 | Update `control/daemon/command.py`: after `_ensure_daemon_running`, send `api.runtime.ready`; raise `ServerReadinessError` on failure or non-ready. Update `test_daemon.py` with the relaunch + ready case. | New case passes; existing daemon tests pass. |
| 7 | Update `control/ops/setup.py` to assert `api.runtime.ready` post-`build_workspace_base` (control_plane.details.manifest_version >= 1). | Setup unit tests (or live-e2e gate equivalent) pass. |
| 8 | Extend `test_bundle_upload.py` required-paths with `runtime/health_handlers.py`. | Test passes. |
| 9 | Write Phase 06 implementation report: explicitly retire cross-server crash isolation; document the in-process crash/restart contract from §4.3; explain why per-server health verbs are NOT introduced (only command-exec has a host-facing data surface). | Report exists, links Phase 04/05 reports. |
| 10 | Final verification: `pytest -q`, `ruff check`, `mypy` on the touched modules. | All green. |

Suggested commit grouping: **5 atomic commits** —
1. Step 1 (health_handlers module with the three private probes).
2. Steps 2 + 3 (dispatcher wires `api.runtime.ready` + routing invariants).
3. Steps 4 + 5 (stale staging fence + first-instantiation hook).
4. Steps 6 + 7 (control daemon readiness gate + setup wiring).
5. Steps 8 + 9 + 10 (bundle test + report + final verification).

## 11. Pass Bar (post-Phase-05 invariants)

The original simplified-plan pass bar listed routing rules
(`read_file → layer-stack-server`, `write/edit → occ-server`) that Phase
05 §6 explicitly retired. Those rows are obsolete and replaced below.

| Invariant (post-Phase-05) | Phase 06 enforcement |
|---|---|
| **command-exec-server is the single host-facing data API surface**: `api.read_file`, `api.write_file`, `api.edit_file`, and `api.shell` all dispatch to command-exec-server modules | Routing-invariants test (§5 step 5) asserts every public data verb resolves to `runtime/write_edit_handlers.*` or `runtime/command_exec_server.shell`. Negative branch asserts no `api.write_*`/`api.edit_*`/`api.read_*` is registered against occ_server / occ_handlers. |
| **layer-stack-server is host-callable only for control-plane ops**: `api.workspace_binding`, `api.{build,ensure}_workspace_base`, `api.{prepare,release}_workspace_snapshot` (plus diagnostic `api.layer_metrics`) | Same routing-invariants test. |
| **occ-server is internal; OCC is reached only through `OCCClient.apply_changeset`** | Phase 03 import-fence test + Phase 05 `test_mutation_gate.py` (unchanged in this phase). Phase 06 negative invariant restated above. |
| In-workspace classifier predicate lives only on command-exec | Inherited from Phase 05 `test_mutation_gate.test_occ_handlers_module_does_not_classify_paths` (unchanged). |
| `command-exec-server` calls `OCCClient`, never `OccService` directly | Phase 04 `test_capture_to_occ_client.py` and Phase 05 `test_shell_capture_atomicity.py` (unchanged). |
| Workspace binding / active-manifest validity is enforced before every guarded data op | Phase 05 `require_workspace_binding(...)` in `write_edit_handlers` and `command_exec_server._command_request` (unchanged). Phase 06 surfaces "not bound" or "manifest missing" as `api.runtime.ready` ⇒ `ready=false` with the failing probe in `details`. |
| `layer-stack-server` never imports OCC, command-exec, Git | Phase 03 `test_import_fence.py` keeps passing. Phase 06 only adds `fence_stale_staging`, which uses no OCC or command-exec symbols. |
| Setup creates workspace truth before guarded APIs run | Step 7 wires `api.runtime.ready` into `control/ops/setup.py` post-`build_workspace_base`. |
| Active leases survive squash and GC | Phase 08 territory; Phase 06's restart contract states leases are dropped on daemon restart (in-memory-only state), consistent with squash/GC running only on a live daemon. |
| Daemon crash leaves on-disk CAS intact and is recoverable through readiness probe | §4.3 in-process crash contract + step 6 (`ServerReadinessError`) + step 4 (`fence_stale_staging`). |
