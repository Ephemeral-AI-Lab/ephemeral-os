# Sandbox Plugin Service Adversarial Implementation Plan

**Status:** In progress; contract/status slice landed, process-backed PPC and
refresh execution remain open.
**Date:** 2026-06-01.
**Scope:** `/sandbox` Rust plugin implementation, with the Python sandbox plugin
path as the behavioral reference.

## Source Anchors

- Python plugin reference:
  `backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py`,
  `overlay_dispatch.py`, `overlay_child.py`, `runtime_api.py`, `projection.py`.
- Python LSP reference:
  `backend/src/plugins/catalog/lsp/runtime/session_manager.py`,
  `pyright_session.py`, `namespace_remount.py`, `apply.py`.
- Workspace and watch reference:
  `backend/src/sandbox/ephemeral_workspace/pipeline.py`,
  `backend/src/sandbox/ephemeral_workspace/events.py`,
  `docs/architecture/sandbox/plugins.html`.
- Workspace materialization reference:
  `backend/src/sandbox/layer_stack/stack.py::LayerStack.commit_to_workspace`,
  `backend/src/sandbox/daemon/layer_stack_runtime.py::commit_to_workspace`,
  `backend/src/sandbox/daemon/builtin_operations.py::commit_to_workspace`,
  `backend/tests/unit_test/test_sandbox/test_layer_stack/test_commit_to_workspace.py`,
  `backend/tests/live_e2e_test/sandbox/workspace_base/test_commit_to_workspace_correctness_perf.py`.
- Rust migration state:
  `sandbox/crates/eos-plugin`, `sandbox/crates/eos-daemon`,
  `sandbox/docs/contract/06-crate-map-and-invariants.md`,
  `docs/plans/sandbox-rust-external-migration-PLAN.md`,
  `docs/plans/sandbox-rust-external-migration-PROGRESS.md`.

## Progress Update - 2026-06-01 23:22 CST

Landed:

- Added `eos-plugin` contract modules for generic plugin services:
  `manifest.rs`, `refresh.rs`, `service.rs`, and `service_registry.rs`.
  These define `PluginServiceKey`, `ServiceMode`,
  `RefreshStrategy`, manifest validation, the
  `workspace_snapshot_refresh` daemon-to-harness messages, and stale-manifest
  health checks.
- Added the daemon plugin module and registered `api.plugin.ensure` /
  `api.plugin.status` in `eos-daemon`. The Rust daemon now records logical
  plugin manifests/services, reports status, keeps the no-`eos-occ` plugin
  dependency edge, and applies the plugin-family isolated-workspace gate before
  ensure/status.
- Added exact registered-op resolution for manifest-declared
  `plugin.<plugin>.<op>` names. Registered ops now return a structured
  `plugin_dispatch_deferred` response instead of `unknown_op`; undeclared
  `plugin.*` names still return `unknown_op`, and digest reload replaces the
  previous route set.
- Added the first daemon PPC/process boundary slice:
  `sandbox/crates/eos-daemon/src/plugin/process.rs` derives per-service
  `/eos/plugin/ppc/*.sock` endpoints and harness environment from
  `PluginServiceKey`, `api.plugin.ensure/status` now expose `service_processes`,
  and `plugin/ppc_router.rs` performs message-id checked AF_UNIX request/reply.
  Connected read-only routes can now dispatch through PPC without holding the
  daemon plugin registry lock during I/O.
- Added opt-in service process lifecycle behind `api.plugin.ensure`:
  `start_services: true` spawns service commands with the PPC harness
  environment, reports `running_service_processes`, and tears processes down
  through the daemon registry/drop path. This proves daemon ownership of service
  lifetime without requiring Pyright in focused tests.
- Added focused Rust coverage: `cargo test -p eos-plugin` (`26 passed`) and
  `cargo test -p eos-daemon plugin` (`13 passed`).
- Added live plugin refresh coverage at
  `backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`,
  backed by `backend/scripts/bench_plugin_refresh_strategies.py`, with
  iteration notes in
  `backend/tests/live_e2e_test/sandbox/plugin/ITERATION-REPORT.md`.
- Live verification passed:
  `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=xingyaoww/sweb.eval.x86_64.dask_s_dask-10042:latest EOS_PLUGIN_REFRESH_SAMPLES=1 EOS_PLUGIN_REFRESH_AUTO_SQUASH_WRITES=104 uv run pytest -q -x -rs --tb=short --durations=10 backend/tests/live_e2e_test/sandbox/plugin/test_plugin_refresh_strategies.py`
  (`1 passed in 12.38s` on the latest rerun).

Still open:

- Real plugin harness accept/connect wiring, concurrent request multiplexing,
  callback servicing, WRITE_ALLOWED route execution, and crash/teardown
  hardening.
- Actual `workspace_snapshot_refresh` namespace remount/restart execution in
  Rust; current Rust service state is the validated logical/status surface.
- `WRITE_ALLOWED` overlay wrapping and self-managed plugin commit callbacks
  still need to stop returning typed deferred errors.
- Non-LSP dummy service parity and Pyright/LSP adapter parity remain required
  before claiming arbitrary-package support.

## Success Criteria

1. Plugin services are generic. The Rust implementation cannot assume Pyright,
   LSP, Python importlib, Node, or any package-specific lifecycle.
2. Plugin tools are shared-ephemeral only. If the caller has an active
   `isolated_workspace` handle, `api.plugin.*` and `plugin.*` operations fail
   with `forbidden_in_isolated_workspace`.
3. A long-running plugin service never serves a stale workspace silently. Each
   tool call is either against the active LayerStack manifest generation or
   fails with a retryable stale-projection error.
4. The generic read-only service path is daemon-managed
   `workspace_snapshot_refresh`. Arbitrary packages run behind a small service
   harness that speaks the daemon refresh protocol; package-native file watching
   may be used as an internal optimization, but not as the correctness source.
5. The read-only service path never publishes. Write-capable plugin tools, when
   a plugin also exposes them, publish only through the daemon-owned
   LayerStack/OCC path. Self-managed plugin callbacks must use the same
   per-`layer_stack_root` single OCC writer and storage lock as primary
   publishes.
6. O(1) overlay behavior remains the default for read-only services and
   one-shot write workers. A materialized filesystem-watch projection is not the
   target architecture. Do not reintroduce a 110-layer runtime overlay guard;
   keep the kernel `OVL_MAX_STACK = 500` ceiling and operational squash
   telemetry model.

## Current State

The Python path already has the right behavioral split but not the right generic
runtime boundary:

- `Intent.READ_ONLY` handlers run in process and must query a long-lived service.
- `Intent.WRITE_ALLOWED` handlers default to a per-operation overlay plus OCC
  publish.
- `auto_workspace_overlay=False` lets LSP apply/rename/format manage its own
  overlay and publish path.
- Pyright gets a private overlay namespace, remounts when the manifest key
  changes, and receives `workspace/didChangeWatchedFiles` plus open-document
  sync events.
- The daemon dispatcher blocks plugin-family operations while isolated mode is
  open for the agent.

The Rust path is deliberately incomplete:

- `eos-plugin` has the registry, dispatch-mode selection, PPC envelope framing,
  no-`eos-occ` crate edge, warm-server registry scaffolding, generic service
  manifests, refresh messages, service keys, and logical service status.
- `dispatch_read_only`, `dispatch_write_allowed`, and
  `dispatch_self_managed` still return typed deferred errors.
- `eos-daemon` registers `api.plugin.ensure` and `api.plugin.status`, records
  manifest-declared services and operation routes, and resolves exact
  `plugin.<plugin>.<op>` names. Read-only routes with a connected PPC client
  perform a message-id checked AF_UNIX round trip; otherwise registered routes
  still return a structured `plugin_dispatch_deferred` response. With
  `start_services: true`, service commands are spawned and reported as daemon
  owned processes, but the generic harness accept/connect loop is not wired yet.
- The compatibility `WarmServerRegistry` is still keyed by `layer_stack_root`
  only. The daemon-owned service registry must use `PluginServiceKey` so
  arbitrary plugin packages with distinct payload digests, runtimes,
  environment, and service modes do not share incompatible processes.

## Design Decision

Implement a generic `PluginServiceRegistry` in the daemon, backed by
`eos-plugin` contracts, with service instances keyed by:

```text
PluginServiceKey {
  layer_stack_root,
  workspace_root,
  plugin_id,
  plugin_digest,
  service_id,
  service_profile_digest,
  service_mode,
  refresh_strategy,
}
```

The registry owns process lifetime, PPC routing, projection freshness, event
subscriptions, and teardown. A service instance is not "the Pyright session"; it
is a daemon-managed read-only process behind the unified refresh protocol.
`service_profile_digest` covers launch command, environment, protocol version,
service mode, and refresh strategy so reuse cannot cross incompatible services.

Plugin manifests should describe:

- `plugin_id`, `plugin_version`, and content digest.
- `service_id` plus the service profile digest.
- Runtime launch command and payload requirements.
- PPC protocol version, using the existing newline-delimited daemon envelope
  framing.
- Service role:
  - `readonly_service` uses `workspace_snapshot_refresh`.
  - `write_worker` uses `oneshot_overlay` or self-managed daemon callbacks.
- Refresh strategy for read-only services:
  - `remount_workspace_and_notify`
  - `remount_workspace`
  - `restart_service`
- Operation list with `Intent`, `auto_workspace_overlay`, timeout, and whether
  the operation needs a warm service or an operation worker.

The service mode names the daemon-owned freshness model. The strategy names are
mechanism names; `refresh_strategy` already supplies the refresh context, so the
enum values should not repeat it.

## Rust Crate Reuse and File Layout

Yes, reuse `eos-ephemeral`, but only as the shared ephemeral contract crate. The
current checkout intentionally keeps `sandbox/crates/eos-ephemeral` small: it
exports `OccRuntimeServicesPort`, `PublishedFile`, and `EphemeralError`, and it
does not own the concrete overlay, LayerStack, OCC, runner, plugin registry, or
route model. Keep that boundary.

Plugin refresh should therefore reuse `eos-ephemeral` for the write/self-managed
publish contract only: both `WRITE_ALLOWED` plugin workers and self-managed PPC
callbacks must publish through the daemon-injected `OccRuntimeServicesPort`.
Do not move `workspace_snapshot_refresh` runtime orchestration into
`eos-ephemeral`.

The resulting crate ownership should be:

```text
sandbox/crates/eos-ephemeral/src/
  lib.rs                 # unchanged public contract surface
  error.rs               # shared ephemeral errors
  ports.rs               # OccRuntimeServicesPort + PublishedFile

sandbox/crates/eos-plugin/src/
  lib.rs                 # exports plugin contracts
  context.rs             # per-call identity and intent
  dispatch.rs            # mode selection and deferred dispatch contracts
  error.rs               # plugin errors
  manifest.rs            # NEW: plugin/service manifest validation
  ppc.rs                 # PPC envelope over eos-protocol framing
  refresh.rs             # NEW: Prepare/Quiesce/Swap/Notify/Resume/Health types
  registry.rs            # op registration and public plugin.* names
  service.rs             # NEW: PluginServiceKey, ServiceMode, RefreshStrategy
  service_registry.rs    # NEW: logical registry contract, no daemon I/O
  warm_server.rs         # evolves into service process handle compatibility

sandbox/crates/eos-daemon/src/
  plugin/mod.rs          # NEW: daemon plugin module boundary
  plugin/ops.rs          # FUTURE: split api.plugin.ensure/status + plugin.* ops
  plugin/service_registry.rs
                         # NEW: live PluginServiceRegistry implementation
  plugin/process.rs      # NEW: PluginServiceKey -> /eos/plugin/ppc/*.sock spec
  plugin/snapshot_refresh.rs
                         # NEW: leased snapshot refresh/remount/restart logic
  plugin/ppc_router.rs   # NEW: message-id checked PPC round trip
  plugin/occ_callbacks.rs
                         # NEW: implements self-managed commit via same OCC port
  plugin/telemetry.rs    # NEW: refresh, lease, queue, restart metrics
```

`eos-plugin` may depend on `eos-ephemeral`, `eos-layerstack`, and
`eos-protocol`. It must not depend on `eos-occ`, `eos-overlay`, `nix`, or
`tokio`. The daemon is the impure owner that combines `eos-layerstack`,
`eos-overlay`, `eos-occ`, `eos-runner`, and `eos-plugin` into a live service.

## Service Modes

### 1. `workspace_snapshot_refresh`

This is the unified mode for arbitrary read-only plugin services.

The contract is between the daemon and the plugin service harness, not between
the daemon and a package-specific protocol like LSP. The harness may wrap
Pyright, ripgrep-indexers, symbol servers, test discovery daemons, or other
package-specific processes. The daemon controls freshness through a standard
refresh protocol:

```text
PrepareRefresh { target_manifest_key }
Quiesce { request_id }
SwapWorkspace { layer_paths, workspace_root, manifest_key }
NotifyRefresh { changed_paths | full_resync }
Resume { request_id }
Restart { reason }
Health { manifest_key }
```

Flow:

1. Start the service in a private namespace backed by a leased read-only
   workspace overlay.
2. Track `manifest_key` on the service handle.
3. Subscribe to daemon workspace-change events.
4. Before every request, run `ensure_service_current(target_manifest_key)`.
5. If the active manifest changed, acquire a fresh snapshot and refresh the
   service according to its strategy:
   - `remount_workspace_and_notify`: quiesce, remount the service namespace,
     send the daemon refresh notification, then resume.
   - `remount_workspace`: quiesce, remount, invalidate daemon-side request caches,
     then resume. The service must read the filesystem on demand and not rely on
     stale internal indexes for correctness.
   - `restart_service`: terminate and restart the service on the new
     snapshot. This is the generic fallback for arbitrary packages with no safe
     refresh API.
6. If refresh fails, do not answer from stale state. Retry, restart, or return a
   retryable `plugin_projection_stale` error.

This keeps the correctness rule generic: the daemon owns the current manifest
generation and the service must prove it is on that generation before serving a
read.

Package-native file watching is optional. It may improve internal cache
latency, but the daemon refresh protocol is authoritative. A service that only
supports raw OS watches can still be supported through `restart_service`;
that is slower than an adapter-specific refresh hook, but it is generic and
does not require a materialized projection as the default.

### 2. `oneshot_overlay`

Use this for stateless tools and normal write-capable plugin tools.

Flow:

1. Acquire the latest LayerStack snapshot.
2. Mount a fresh per-operation overlay at `workspace_root`.
3. Run the plugin worker inside that namespace.
4. Capture upperdir changes for `WRITE_ALLOWED`.
5. Publish through the daemon's single OCC writer.
6. Release lease and scratch.

This is the generic equivalent of Python `overlay_dispatch.py`. It has the best
freshness story and no watch problem because each invocation starts from the
latest snapshot.

## Freshness Algorithm

Every service handle maintains:

```text
active_manifest_key
active_manifest_version
refresh_strategy
projection_state = current | refreshing | stale | restarting
last_refresh_error
queue_lag
```

Before dispatching a plugin operation:

1. Read the active LayerStack manifest key.
2. If the service key is current, dispatch.
3. If not current, run `ensure_service_current(target_manifest_key)`.
4. If refresh succeeds, dispatch and include the manifest key in telemetry.
5. If refresh fails, return a retryable `plugin_projection_stale` or restart the
   service, depending on operation policy.

Concurrency rules:

- One refresh/update lock per service instance.
- Requests may wait behind refresh up to a bounded timeout.
- Never hold a daemon-wide registry lock across service I/O.
- Use a latest-value channel for manifest targets and a bounded queue for path
  deltas. Queue overflow becomes `NotifyRefresh { full_resync }`, restart, or a
  retryable stale error; it is never silent event loss.

## Overlay and OCC Workflow

Read-only `workspace_snapshot_refresh` workflow:

1. The daemon acquires a LayerStack snapshot lease for the active shared
   ephemeral workspace.
2. The daemon starts or refreshes the service in a private namespace with a
   read-only overlay projection of that snapshot.
3. Before each request, the daemon compares the service manifest key with the
   active LayerStack manifest key.
4. If stale, the daemon refreshes the service by quiescing it, swapping or
   remounting the projection, notifying or restarting the harness, then
   resuming requests.
5. The service answers read-only requests only after reporting the target
   manifest key through `Health`.

This path does not go through OCC because it does not publish. It only consumes
leased snapshots and daemon-owned refresh events.

Write-capable plugin workflow:

1. The daemon acquires the latest LayerStack snapshot.
2. The daemon mounts a fresh per-operation overlay for the worker/apply step.
3. The worker writes into that upperdir.
4. The daemon captures the upperdir result and publishes through the existing
   per-root OCC writer.
5. The daemon releases the lease and scratch state.

So yes: for a write operation, mount first, then publish through OCC. The
long-lived read-only service may compute an edit plan, but it cannot own the
write mount or publish directly.

Sharing rule:

- Multiple operations from the same `PluginServiceKey` may share one
  `workspace_snapshot_refresh` process.
- Multiple plugin services on the same `layer_stack_root` may share daemon-side
  latest-manifest observation, event coalescing, and snapshot-acquire work.
- They must not share process memory, namespace state, PPC sessions, service
  caches, upperdirs, or OCC writers.

## `commit_to_workspace` as a Watcher Bridge

Candidate idea: have the daemon periodically call `api.commit_to_workspace` so a
plugin service watching the target workspace receives native filesystem events.

Assessment: do not use this as the default plugin refresh mechanism.

Current code behavior:

- `LayerStack.commit_to_workspace()` projects the active manifest into a fresh
  rendered tree, replaces the target workspace contents, clears layer-stack
  storage, then rebuilds a fresh base layer from the workspace bytes.
- It refuses to run while any snapshot lease is active with
  `RuntimeError("commit_to_workspace blocked by active leases")`.
- The daemon wrapper documents this as a privileged tear-down sync operation,
  not a steady-state refresh path.

Implications for plugin services:

- A correctly managed long-lived read-only service normally holds a leased
  snapshot/projection. That active lease blocks `commit_to_workspace`.
- Forcing periodic commits would either skip whenever useful work is active, or
  require dropping service leases and remounts on a timer. That turns a refresh
  protocol into repeated global workspace materialization.
- The operation is O(repository bytes) because it renders the merged view and
  rewrites the target workspace. That violates the desired steady-state O(1)
  overlay refresh model.
- Workspace watchers may receive events, but they would see whole-tree replace
  churn, not a precise semantic changed-path stream. This can cause unnecessary
  reindexing and event storms.
- Because commit resets layer storage and rebuilds base, running it while other
  tool calls, background shells, plugin services, or snapshot readers are active
  is intentionally disallowed by the active-lease guard.

Use `commit_to_workspace` only for explicit materialization boundaries, such as
SWE-EVO evaluation or final handoff where active leases have drained. Treat a
daemon timer that calls it every few seconds as a rejected default unless the
experiments below prove a narrowly bounded maintenance mode.

Experiment gates before any periodic-commit mode can be considered:

1. **Lease refusal gate:** hold a plugin-service snapshot lease and verify a
   periodic `api.commit_to_workspace` attempt fails or skips without killing the
   service, leaking a lease, or changing the manifest.
2. **Auto-squash gate:** drive manifest depth beyond the auto-squash threshold,
   trigger squash, then commit after leases drain. Verify raw workspace bytes,
   `.git` preservation, manifest depth, orphan count, and missing-layer count.
3. **Concurrent work gate:** run background shell, direct write/edit, read-only
   service calls, and self-managed plugin callbacks while the periodic committer
   wakes up. Expected behavior is skip/defer while leases or in-flight writes
   exist, not force commit.
4. **Watcher usefulness gate:** run a non-LSP watcher harness on the raw
   workspace and measure whether commit events actually refresh its cache
   correctly. Also measure event count and reindex time; whole-tree churn above a
   small threshold kills the approach.
5. **Throughput gate:** compare tool p95/p99 latency and storage bytes with and
   without a 2s commit timer on a large workspace. Any regression to foreground
   mount/read/write latency or storage lock wait kills the approach.

Expected outcome: this likely fails as a general plugin-service solution because
the active-lease guard and full projection semantics are working as designed.
It may remain useful as an explicit "materialize to target workspace now" API,
not as the freshness source for long-running read-only services.

### Experiment Result - 2026-06-01

Harness:

- Script: `backend/scripts/bench_plugin_refresh_strategies.py`
- Existing container: `2856103e0c53`
- Experiment paths: `/eos/plugin/workspace`, `/eos/plugin/layer-stack`, and
  watcher files under `/eos/plugin/*`
- Transport: daemon TCP endpoint, not Docker exec for measured daemon calls
- Artifacts:
  `bench/plugin-refresh-strategies-20260601.json`,
  `bench/plugin-refresh-strategies-20260601.md`

Results:

- `workspace_snapshot_refresh` refreshed through acquire/release/read in
  p95 `5.747 ms` and never served stale content.
- `commit_to_workspace_timer` materialized in p95 `11.419 ms` on this small
  workspace, and did produce native watcher events.
- `raw_workspace_fs_watch` without materialization stayed stale: daemon reads saw
  `watch-no-commit`, raw workspace still had `initial`, and the watcher saw
  zero target events.
- A synthetic held snapshot lease was not observed by the current
  `api.commit_to_workspace` path in this daemon run; commit succeeded and reset
  storage. That means a periodic materializer would need an explicit daemon
  plugin-service guard before it can be considered safe around long-lived
  plugin services.
- Auto-squash plus post-drain commit passed: after 104 writes, pre-commit
  manifest depth was `10` at version `111`; post-commit manifest depth was `1`,
  raw bytes matched the daemon view, and orphan/missing layer counts were `0`.

Conclusion:

Use `workspace_snapshot_refresh` as the default. It is faster on measured
refresh, does not require raw workspace materialization, and gives the daemon a
generic place to enforce freshness before reads. `commit_to_workspace` remains
an explicit materialization boundary, not a timer. `raw_workspace_fs_watch` is
not correct by itself because LayerStack publishes do not mutate the raw
workspace.

## Write Semantics

The `workspace_snapshot_refresh` service is read-only. It can answer queries or
return an edit plan, but it does not mutate workspace truth and does not publish.
Write-capable plugin tools must use a separate daemon-owned write path.

Allowed write paths:

1. `WRITE_ALLOWED` with `auto_workspace_overlay=true`: daemon acquires a fresh
   operation overlay, runs a worker/adapter, captures upperdir, and publishes
   through OCC.
2. Service-query-plus-daemon-apply: a warm service returns an edit plan, then the
   daemon applies it inside a fresh operation overlay and publishes.
3. `auto_workspace_overlay=false`: the service uses PPC callbacks for advanced
   self-managed apply, but those callbacks route into the same daemon-owned
   per-root OCC writer and storage lock.

Rejected paths:

- Capturing the long-lived read-only service overlay.
- Letting a plugin service write directly into LayerStack.
- Creating a second OCC service, commit queue, or storage writer for plugin
  callbacks.
- Allowing plugin operations while isolated workspace is active.

## Adversarial Review Loop

### Round 1 - Overfit Critic

Critique: The current PPC plan still reads as "Pyright in a wrapper." A generic
plugin service cannot depend on LSP notifications, Pyright remount behavior, or
Python importlib compatibility.

Resolution:

- Promote `readonly_service` plus `workspace_snapshot_refresh` into the
  manifest contract.
- Require a non-LSP daemon-refresh probe before declaring generic read-only
  service support.
- Key service instances by plugin identity and digest, not just
  `layer_stack_root`.

### Round 2 - File-Watch Critic

Critique: Overlay remount plus synthetic LSP-style notifications does not
satisfy arbitrary packages that use inotify or similar filesystem watchers.
They may hold inode watches that do not map cleanly across remounts.

Resolution:

- Do not make package-native file watches the correctness contract.
- Define a daemon-to-harness refresh protocol that every read-only service must
  implement.
- Let package adapters choose `remount_workspace_and_notify`,
  `remount_workspace`, or `restart_service`.
- Validate with a non-LSP dummy service that caches file content and proves the
  daemon refresh protocol invalidates or restarts it before the next read.

### Round 3 - Space-Model Critic

Critique: A generic daemon-managed service could drift toward materializing a
full workspace projection to satisfy file watchers, breaking the sandbox O(1)
overlay promise.

Resolution:

- Keep `workspace_snapshot_refresh` on leased LayerStack lowerdirs plus a
  service-private read-only overlay/remount path.
- Treat materialized projections as a rejected default and a future escape hatch
  only if a separate plan proves bounded space.
- Report service lease count, layer path count, refresh count, remount count,
  restart count, and queue lag in plugin telemetry.
- Add a gate that repeated peer publishes do not grow service workspace bytes
  except bounded scratch metadata.

### Round 4 - Publish-Correctness Critic

Critique: Self-managed plugin callbacks create a second structural entry point
to OCC. If that callback constructs its own writer, parity tests can pass while
contention correctness is broken.

Resolution:

- Keep `eos-plugin` free of `eos-occ`.
- Have `eos-daemon` own the only concrete OCC service cache.
- Pass the same per-root OCC runtime services into both primary plugin writes and
  self-managed callback handling.
- Add concurrent interleave tests: self-managed plugin writes plus direct
  write/edit plus shell publishes.

### Round 5 - Isolation Critic

Critique: "Plugin tools only under ephemeral workspace mode" can be weakened if
`api.plugin.ensure` or `api.plugin.status` bypass the isolated gate.

Resolution:

- Treat every `api.plugin.*` and `plugin.*` op as plugin-family.
- Extract `agent_id` from the daemon envelope.
- If that agent has an active isolated handle, return
  `forbidden_in_isolated_workspace` before ensure, status, warm start, or tool
  dispatch.
- Preserve no-agent legacy status only for daemon diagnostics that do not observe
  an agent workspace.

## Implementation Phases

### Phase 0 - Contract Tightening

- Extend `sandbox/docs/contract/01-wire-protocol.md` with `api.plugin.ensure`,
  `api.plugin.status`, dynamic `plugin.*`, and PPC callback response shapes.
- Extend `sandbox/docs/contract/03-audit-and-metrics.md` with generic plugin
  service telemetry, avoiding Pyright-specific event names.
- Extend `sandbox/docs/contract/06-crate-map-and-invariants.md` with
  `PluginServiceKey`, read-only refresh strategies, and the `eos-plugin`
  no-`eos-occ` guard.

Checks:

- `cargo tree -p eos-plugin --edges normal` has no `eos-occ`.
- Existing `eos-plugin` unit tests still pass.

### Phase 1 - Daemon Plugin Surface

- Register `api.plugin.ensure` and `api.plugin.status` in `eos-daemon`.
- Add dynamic registration for `plugin.<plugin>.<op>`.
- Add the plugin-family isolated gate in Rust before handler dispatch.
- Replace `WarmServerRegistry` with or wrap it in `PluginServiceRegistry` keyed
  by `PluginServiceKey`.

Checks:

- Unit tests for keying, registration conflict, status shape, digest reload, LRU
  eviction, and isolated blocking.

### Phase 2 - Process-Backed PPC

- Spawn plugin service processes as process groups. The focused
  `start_services: true` lifecycle is landed; real harness socket handoff,
  heartbeat, and crash recovery remain.
- Connect through AF_UNIX PPC using the existing envelope framing. The focused
  single-request route is landed for connected read-only services; process
  accept/connect handoff and concurrent multiplexing remain.
- Support message-id matched request/reply and plugin-to-daemon callbacks.
- Add explicit teardown, timeout, heartbeat, and crash recovery.

Checks:

- PPC round trip with message-id matched reply.
- Mismatched message id rejection.
- Service crash returns structured plugin error and reaps process group.
- No daemon registry lock is held during PPC I/O.

### Phase 3 - `oneshot_overlay` Writes

- Implement `dispatch_write_allowed` against daemon-owned overlay acquire,
  worker invocation, upperdir capture, and OCC publish.
- Preserve plugin result plus publish metadata.
- Keep service projection out of the publish path.

Checks:

- Python parity for `test_plugin_write_allowed_apply_workspace_edit_publishes`.
- Rust unit/integration test proving one publish through the existing OCC writer.

### Phase 4 - `workspace_snapshot_refresh` Service

- Implement `workspace_snapshot_refresh`.
- Implement the daemon-to-harness refresh protocol:
  `PrepareRefresh`, `Quiesce`, `SwapWorkspace`, `NotifyRefresh`, `Resume`,
  `Restart`, and `Health`.
- Support `remount_workspace_and_notify`, `remount_workspace`, and `restart_service`.
- Port Pyright/LSP as one adapter, not as the service model itself.
- Add a non-LSP read-only dummy service that caches workspace content and only
  stays correct if the daemon refresh protocol works.

Checks:

- Read-only LSP refresh after peer publish, with no plugin publish timing.
- Peer publish plus service refresh without cold restart.
- Evict and ensure starts a new warm service.
- Non-LSP service reads the post-publish content and never serves its cached
  pre-publish value.

### Phase 5 - Read-Only Sharing and Refresh Coalescing

- Share the daemon event subscription, latest-manifest channel, and snapshot
  acquisition across all services for the same `layer_stack_root`.
- Coalesce concurrent refreshes targeting the same manifest key.
- Keep process, namespace, PPC connection, and service cache state isolated per
  `PluginServiceKey`.
- Allow multiple operations from the same plugin service to reuse one read-only
  service instance when plugin id, digest, service id, service profile digest,
  workspace root, and refresh strategy match.

Checks:

- Two services on one workspace observe the same manifest generation after a
  peer publish.
- A refresh failure in one service does not poison another service.
- Shared refresh metadata does not imply a shared upperdir or shared OCC writer.

### Phase 6 - Contention and Parity Gates

- Add AV-10 plugin parity for READ_ONLY, WRITE_ALLOWED, and self-managed modes.
- Add CP-4 interleave with direct writes, shell publishes,
  `workspace_snapshot_refresh` service calls, and self-managed callbacks.
- Add forward/back parity where Python publishes, Rust plugin reads, Rust plugin
  publishes, and Python reads.

Checks:

- Final workspace hash parity.
- Manifest root hash and layer digest parity for publish paths.
- No stale plugin response after peer publish.
- No `forbidden_in_isolated_workspace` bypass.
- No unbounded service workspace growth.

### Phase 7 - Periodic `commit_to_workspace` Kill-Switch Experiment

This is an experiment, not part of the recommended architecture.

- Add an opt-in daemon maintenance task that wakes on a short interval and tries
  to materialize only when no leases, no plugin refresh, and no in-flight
  workspace mutations exist.
- First implementation should be skip-only: if any guard is active, emit
  telemetry and do nothing.
- Never let the timer force-release leases, cancel work, or restart plugin
  services.
- Run the five `commit_to_workspace` gates from the watcher-bridge section.

Checks:

- Existing commit-to-workspace unit tests pass.
- Active plugin service lease blocks or skips periodic commit.
- Auto-squash plus post-drain commit preserves final bytes and layer metrics.
- Background operations complete with no lost writes, no deadlocks, no stale
  plugin response, and no unexpected service restart.
- Watcher event count and foreground tool p99 remain within explicit thresholds.

## Rejected Alternatives

### One-Shot Everything

Reject as the universal design. It is generic and fresh, but it makes Pyright and
other indexing services unusably expensive because every call pays cold start and
full index cost.

### Unmanaged Workspace Remount Long-Lived Services

Reject. A bare remount without the daemon refresh protocol can leave service
caches stale. Remount is allowed only as one step inside
`workspace_snapshot_refresh`.

### Materialized File-Watch Projection As The Default

Reject. It gives watch compatibility but throws away the O(1) overlay path for
read-only services, stateless tools, and normal write workers.

### Let Services Publish Their Own Writes

Reject. It breaks OCC ownership and makes isolated/shared workspace semantics
ambiguous.

### Periodic `commit_to_workspace` For Watch Refresh

Reject as the default. It is a global materialization/reset operation, not a
read-only refresh primitive. It refuses active leases, performs a full merged
projection, rewrites the target workspace, and rebuilds layer-stack base state.
That makes it appropriate for explicit end-of-run materialization, not for a
few-second daemon timer feeding plugin watchers.

## Final Recommendation

Ship one daemon-managed read-only service layer:

1. `workspace_snapshot_refresh` is the unified abstraction for arbitrary
   read-only plugin services.
2. The daemon owns manifest freshness, remount/restart, service health, and
   event coalescing.
3. Service packages run behind a small harness that implements the standard
   refresh protocol. Package-specific APIs such as LSP are adapters behind that
   harness, not daemon assumptions.
4. Write tools stay outside the read-only service path: fresh operation overlay,
   upperdir capture, OCC publish.
5. `commit_to_workspace` remains an explicit materialization boundary. Do not
   put it on a daemon timer for plugin freshness unless the kill-switch
   experiment proves it only skips under active work and has acceptable latency,
   watcher, and auto-squash behavior.

Do not claim generic arbitrary-package support until a non-LSP read-only service
proves `restart_service` or `remount_workspace` correctness under peer publishes,
and Pyright proves `remount_workspace_and_notify` parity without plugin-specific
daemon logic.
