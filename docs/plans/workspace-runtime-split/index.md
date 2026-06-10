# Workspace Runtime Split: Tool-Call-Centric Crates

Status: Proposed
Date: 2026-06-11
Owner: sandbox/crates
Scope: replace `eos-workspace-runtime` (and the daemon glue that props it up)
with tool-call-centric crates: `eos-command-ops`, `eos-file-ops`,
`eos-ephemeral-workspace`, `eos-isolated-workspace`, `eos-command-session`,
`eos-store`. The isolated-workspace JSONL audit pipeline is **dropped during
the migration, not carried over** (see §2.5); the daemon's transport-level
audit ring is a separate subsystem and stays untouched.

The driving rule: **a workspace is an overlay that operations are performed
on — never a thing that runs commands.** Tool families (command session tools,
file tools) own lifecycle and decide what happens to upperdir changes; the
storage gateway owns OCC and the layer stack; workspaces own only overlay
state.

## 1. Diagnosis: what is mixed today

`eos-workspace-runtime` (~8,000 LOC) plus the daemon glue that completes it
(~3,100 LOC across `eos-daemon/src/{workspace,occ,overlay}`) interleave four
concepts:

| Concept | Where it lives today | Why it is wrong |
| --- | --- | --- |
| Command preparation/finalization | `ephemeral/command.rs` (329 LOC) and `isolated/command.rs` (292 LOC), ~85% structurally identical | Workspaces build `RunRequest`s, session dirs, and metadata — workspaces act as command containers. The duplication exists *because* the boundary is wrong. |
| File-op semantics | `contract/file_ops.rs` (409 LOC of implementation, not DTOs) + identical 55-LOC `ops.rs` wrappers in both modes + 388-LOC `eos-daemon/src/workspace/files/ports.rs` | One tool family smeared across three layers and two crates, behind three traits (`WorkspaceFileOps`, `WorkspaceReadView`, `WorkspaceMutationSink`). |
| Storage access | Daemon-implemented ports: `WorkspaceRunHostPorts` (god-port: lease + timings + publish + audit), `WorkspacePublisherPort`, `LayerStackSnapshotPort`; per-root OCC cache in `eos-daemon/src/occ/service_cache.rs` | An ephemeral publish traverses **two stacked ports** (`finalize_ephemeral` → `publish_upperdir_changes`) to reach one `apply_changeset` call. "Ephemeral" file writes never touch an overlay at all — they are direct OCC fast-changes mislabeled as workspace behavior. |
| Isolation environment | `isolated/{network,session}` in the runtime crate, but holder spawn/mount/cgroup behind `NamespaceRuntimePort` implemented back in `eos-daemon/src/workspace/isolated/runtime.rs` | One subsystem (namespaces + veth/nftables + cgroups) split across two crates by a port that exists only to avoid admitting where the code belongs. |
| Isolated audit pipeline | `isolated/audit.rs` (`AuditSink`/`JsonlAuditSink`); the `A: AuditSink` generic threaded through `session/{lifecycle,gc,capacity,persistence}.rs`; `take_isolated_audit` smuggling audit JSON through `WorkspaceCommandOutcome.metadata`; `record_tool_call` on the god-port; hand-built payloads with hardcoded `exit_code: 0` / fake `phases_ms` in `eos-daemon/src/workspace/files/ports.rs:363-388` | Audit data rides inside command outcomes, crosses two crates through three indirections, and parts of the payload are fabricated. **Dropped entirely in this migration** — not redesigned, deleted. |

Eight workspace-runtime port traits exist today; all eight die. One new trait
(`FileBackend`, owned by `eos-file-ops`) and the OCC-internal seams
(`CommitTransactionPort`, `OccRouteProvider`) remain.

## 2. Target architecture

### 2.1 Crate map

```
                         ┌─────────────────────────────────────────────┐
                         │ eos-daemon  (transport, dispatch, plugins,  │
                         │ audit ring, config, composition root)       │
                         └───────┬───────────────┬─────────────┬───────┘
                                 │               │             │ enter/exit/status
                    ┌────────────▼───┐   ┌───────▼────────┐    │
   command tools →  │ eos-command-ops│   │  eos-file-ops  │ ←  │  file tools
   exec_command     │ registry:      │   │ read/write/edit│    │
   write_stdin      │ command_id →   │   │ FileBackend:   │    │
   read_progress    │ {pty, ws bind} │   │  Store|Isolated│    │
   cancel/collect   └─┬────┬───┬───┬─┘   └───┬───────┬────┘    │
                      │    │   │   │         │       │         │
        ┌─────────────▼┐   │   │ ┌─▼─────────▼─┐   ┌─▼─────────▼───────┐
        │eos-command-  │   │   │ │  eos-store  │   │ eos-isolated-     │
        │session (PTY/ │   │   │ │ per-root:   │   │ workspace         │
        │process       │   │   │ │ leases,     │   │ session registry, │
        │substrate)    │   │   │ │ latest read,│   │ ns+net+cgroup env,│
        └──────────────┘   │   │ │ OCC commits │   │ audit, view,      │
                           │   │ └─┬───────┬───┘   │ caps/TTL/GC       │
            ┌──────────────▼─┐ │   │       │       └─────────┬─────────┘
            │ eos-ephemeral- │ │   │       │                 │
            │ workspace      │◄┘ ┌─▼───┐ ┌─▼──────────┐      │
            │ alloc→mount-   │   │eos- │ │eos-        │      │
            │ plan→capture→  │   │occ  │ │layerstack  │      │
            │ discard        │   └──┬──┘ └─────┬──────┘      │
            └───────┬────────┘      └────┬─────┘             │
                    │              ┌─────▼─────┐             │
                    └──────────────►  eos-cas  ◄─────────────┘
                                   │ + overlay │
                                   └───────────┘
```

Allowed edges only point downward. The two load-bearing absences:

- **`eos-ephemeral-workspace` and `eos-isolated-workspace` have no edge to
  `eos-store`/`eos-occ`/`eos-layerstack`.** They receive a `Snapshot` value and
  return captured `LayerChange`s. The isolated crate's build-time no-publish
  guarantee gets *stronger* than today (today the isolated module shares a
  crate with publish-port consumers).
- **`eos-store` has no edge to any workspace or tool crate.** OCC/layer-stack
  knowledge ends there.

### 2.2 Crate responsibilities

| Crate | Responsibility (one sentence) | Public surface (sketch) | Depends on | Est. LOC |
| --- | --- | --- | --- | --- |
| `eos-store` | Per-root authority over durable state: snapshot leases, latest-materialization reads, and OCC-gated commits — the only crate above the engines that touches `eos-occ`/`eos-layerstack`. | `Store::for_root(&Path) -> RootStore`; `RootStore::{acquire_snapshot, release_lease, read_latest, commit_direct(changes, base), publish_capture(&Snapshot, changes)}` | eos-occ, eos-layerstack, eos-cas | ~500 |
| `eos-command-session` | Policy-free PTY/process substrate: spawn under PTY, stdin, progress tail, transcript, signal/reap, current-exe ns-runner spawn. | `CommandSession::{spawn, write_stdin, read_progress, cancel, reap}` + transcript/tail types | eos-config, eos-cas (RunRequest), rustix/nix/tokio (linux) | ~1,200 (moved verbatim) |
| `eos-ephemeral-workspace` | A per-operation overlay transaction: allocate upper/work dirs against a snapshot, expose the mount plan, capture the upperdir delta, discard. | `EphemeralWorkspace::{create(scratch_root, Snapshot), mount_plan() -> MountPlan, capture() -> CapturedChanges, discard()}` (RAII cleanup guard) | eos-overlay, eos-cas | ~350 |
| `eos-isolated-workspace` | The persistent private workspace subsystem: caller-keyed session registry (TTL/caps/GC/persistence) and the namespace holder + veth/nftables/DNS/cgroup env (absorbed from the daemon), exposing the two read surfaces other crates consume. | `IsolatedSessions::{enter(caller, Snapshot, ResourceCaps) -> WorkspaceHandleId, exit -> ExitedWorkspace{lease_id}, status, list_open, binding(id) -> CommandBinding{ns_fds, dirs}, view(id) -> IsolatedView}`; `IsolatedView::{read (upper-first→merged), write_upper}` | eos-overlay, eos-cas, rtnetlink/netlink-sys/nix/rustix (linux) | ~2,500 |
| `eos-command-ops` | The command-session tool family and its lifecycle policy: owns the `CommandId → {pty session, bound workspace}` registry and decides publish (ephemeral) vs retain (isolated) at settle. | `CommandOps::{exec_command(req, ExecTarget), write_stdin, read_command_progress, cancel, collect_completed, count, sweep_expired, cleanup_caller}`; `enum ExecTarget { Ephemeral{root, scratch_root}, Isolated{caller, workspace} }` | eos-command-session, eos-ephemeral-workspace, eos-isolated-workspace, eos-store, eos-cas | ~1,300 |
| `eos-file-ops` | The file tool family: read/write/edit semantics (size caps, base-content conflict detection, search/replace) over a backend that is either the store fast path or an isolated workspace view. | `trait FileBackend { read, base, commit }`; `StoreBackend(RootStore)`, `IsolatedBackend(IsolatedView)`; `read_file/write_file/edit_file<B: FileBackend>` + request/outcome DTOs | eos-store, eos-isolated-workspace, eos-cas | ~600 |

Vocabulary types (`Snapshot` lease DTO, `CallerId`, `InvocationId`,
`WorkspaceHandleId`) move to **`eos-cas`**, which is already the de-facto
vocabulary floor (`Manifest`, `LayerChange`, `LayerPath`, `RunRequest` live
there). No new contract crate: every remaining shared type sits next to the
data model it describes, and `eos-layerstack::Lease` converts into
`eos_cas::Snapshot` at the `eos-store` boundary.

`eos-workspace-runtime` is deleted at the end. `eos-daemon` keeps transport,
auth, dispatch, plugins, the audit ring, and a composition root that constructs
`Store`, `IsolatedSessions`, and `CommandOps` once and hands them to thin
handlers. Response-shaping telemetry (`/proc` + cgroup `base_timings`) stays in
the daemon wire layer — it was never a workspace concern.

### 2.3 Lifecycle flows

`exec_command` on the ephemeral (default) target — the overlay is a
transaction owned by the tool, not a container:

```
eos-daemon            eos-command-ops        eos-store      eos-ephemeral-ws   eos-command-session
 op_exec_command ───► exec_command(Ephemeral)
                        ├─ acquire_snapshot ───►  lease
                        ├─ EphemeralWorkspace::create(snapshot) ──► dirs (upper/work)
                        ├─ build RunRequest{FreshNs, mount_plan}   (runner child mounts overlay)
                        ├─ spawn pty ────────────────────────────────────────► session
                        ├─ registry: command_id → {session, Ephemeral(ws)}
                        │   … write_stdin / read_progress / cancel hit registry only …
                        └─ on reap:
                             ├─ ws.capture() ──► CapturedChanges (LayerChanges + kinds + stats)
                             ├─ publish_capture(snapshot, changes) ──► OCC single writer
                             ├─ ws.discard(); release_lease
                             └─ completion queue
```

`exec_command` on an isolated target — same registry, different binding and
settle policy:

```
 op_exec_command ───► exec_command(Isolated{caller, ws_id})
                        ├─ isolated.binding(ws_id) ──► {ns_fds, scratch dirs}
                        ├─ build RunRequest{SetNs(ns_fds)}
                        ├─ spawn pty ──► session;  registry: command_id → {session, Isolated(ref)}
                        └─ on reap:
                             └─ registry cleanup + completion queue only — workspace retained
                                untouched; no capture, no publish, no lease release ("do nothing")
```

File tools — two backends, no overlay on the fast path:

```
write/edit (direct):    file-ops ─ read_latest base ─ conflict check ─ commit_direct ─► OCC (gated)
read (direct):          file-ops ─ read_latest ──────────────────────► MergedView of active manifest
write/edit (isolated):  file-ops ─ IsolatedView.read base ─ apply ─ write_upper ─ retained
read (isolated):        file-ops ─ IsolatedView.read (upperdir-first, then frozen-lease MergedView)
```

Isolated lifecycle ops (`enter`/`exit`/`status`/`list_open`) are the isolated
workspace's own public API; the daemon handler composes them with the store:
`enter` = `store.acquire_snapshot()` → `isolated.enter(snapshot, caps)`;
`exit` = `isolated.exit(id)` → `store.release_lease(exited.lease_id)`.

### 2.4 Long-lived sessions: yield, settle, sweep

A PTY session is never bound to the RPC that started it. `yield_time_ms`
shapes only the **response**, never the lifecycle: `exec_command` spawns and
registers the session, then waits at most `yield_time_ms` (default 1000ms; it
returns earlier once output has gone quiet for `quiet_ms`, today 50ms). If the
child exited inside the window the response is the settled result; otherwise
the response is `running { command_id }` and the session keeps running in the
registry — for hours if need be (`max_session_s` backstop, default 6h).

**Settle** is the once-only post-exit path. Ephemeral: capture upperdir →
`publish_capture` → discard dirs → release lease. Isolated: registry cleanup
only. Five triggers race to observe the exit; the first one runs settle and
the registry transition guarantees exactly-once (later callers fall through to
the completion queue):

| Trigger | When it settles |
| --- | --- |
| `exec_command` yield-wait | child exits within `yield_time_ms` |
| `write_stdin` yield-wait | child exits within the request's window after stdin |
| `read_command_progress` poll | poll observes the reaped child |
| `cancel` | SIGTERM→KILL, waits `cancel_wait_ms` (500ms); caller-initiated, ephemeral discards without publishing |
| periodic reaper sweep | **the only finalizer for fire-and-forget sessions**: settles exited-but-never-polled sessions and enforces the `max_session_s` wall clock (sessions started without an explicit timeout get the cap, so nothing runs forever) |

Completions park in the bounded completion queue (1024, LRU drop) and drain
via `collect_completed` — the heartbeat for clients that went away and came
back. All of this is `eos-command-ops`: the registry, the settle paths, the
sweep, and startup recovery (orphaned session metadata from a previous daemon
becomes parked `orphan_reaped` completions; old children are reclaimed by
their own runner timeout, leases by LayerStack GC). The daemon contributes
exactly two hooks: a transport timer that ticks `CommandOps::sweep_expired`
(today `transport/server.rs:180`) and a startup call to recovery (`:189`).
There is no separate "command_session_manager" — `CommandOps` *is* that
manager; a second one would split ownership of the registry again.

One consequence to keep visible: a long-lived ephemeral session holds its
snapshot lease for its whole lifetime, which pins layer-stack GC/squash at the
lease head. That is by design — publish needs the snapshot base to gate
against — and the sweep's wall clock is what bounds it.

### 2.5 Port-trait kill list

| Today | Fate | Replaced by |
| --- | --- | --- |
| `WorkspaceRunHostPorts` (run/ports.rs) | **deleted** | `eos-command-ops` calls `eos-store` and workspace crates concretely; daemon wire layer splices telemetry |
| `WorkspacePublisherPort` (ephemeral/ports.rs) | **deleted** | `RootStore::publish_capture` |
| `LayerStackSnapshotPort` (isolated/session/ports.rs) | **deleted** | `Snapshot` passed into `enter`; lease released by the caller via `eos-store` |
| `NamespaceRuntimePort` (isolated/session/ports.rs) | **deleted** | concrete holder/net/cgroup code inside `eos-isolated-workspace` (absorbs `eos-daemon/src/workspace/isolated/{runtime,ns_runner,state}.rs`) |
| `WorkspaceFileOps`, `WorkspaceReadView`, `WorkspaceMutationSink` (contract) | **deleted** | single `FileBackend` trait owned by `eos-file-ops`, two impls |
| `AuditSink` (isolated/audit.rs) | **deleted** | nothing — the JSONL audit pipeline is dropped wholesale: `AuditSink`/`JsonlAuditSink`, `IsolatedSession::record_tool_call`, `take_isolated_audit` (isolated/command.rs:220) and the audit block in `WorkspaceCommandOutcome.metadata`, the daemon's `record_tool_call` free fn + `record_isolated_tool_call` (files/ports.rs:363-388), and the `isolated_workspace.audit_jsonl_path` config knob in `eos-config` |
| `CommitTransactionPort`, `OccRouteProvider` (eos-occ) | kept | OCC-internal seams, unchanged |

`WorkspaceMode` as a dispatch flag disappears with them: mode becomes the
typed `ExecTarget` / backend choice at the tool boundary, and the registry's
`WorkspaceRun`-style enum stays the only place both arms meet.

Killing the two ports and the audit sink together collapses
`IsolatedSession<S: LayerStackSnapshotPort, R: NamespaceRuntimePort, A:
AuditSink>` — and the daemon's `DaemonSession` alias that instantiates it
(`workspace/isolated/state.rs:18`) — into the single concrete
`IsolatedSessions` type. All three generic parameters existed only to carry
the seams this plan removes.

**Out of scope, deliberately:** the daemon's transport-level audit ring
(`eos-daemon/src/audit/`, `transport/tool_call_events.rs`, the `api.audit.pull`
op) is a different subsystem — op-lifecycle events emitted by dispatch, and
the tap the e2e harness uses to assert storage behavior (e.g. no `occ.publish`
during isolated runs). It does not consume the workspace audit pipeline and is
untouched here.

## 3. Migration map (today → target)

| Today | Target |
| --- | --- |
| `eos-workspace-runtime/src/command_session/**` | `eos-command-session` (verbatim move) |
| `eos-workspace-runtime/src/run/{manager,registry,ports,isolated_command_handle}.rs` | `eos-command-ops` (manager+registry rewritten around `command_id`; ports deleted) |
| `eos-workspace-runtime/src/ephemeral/{command,ops}.rs` + `isolated/{command,ops}.rs` | folded into `eos-command-ops` (one prepare/settle path parameterized by `ExecTarget`) and `eos-file-ops`; the ~600 LOC duplicate pair is deleted |
| `eos-workspace-runtime/src/ephemeral/{dirs,types,capture,finalize,timings,error}.rs` | `eos-ephemeral-workspace` |
| `eos-workspace-runtime/src/isolated/{session/**,network/**,caps,error}.rs` | `eos-isolated-workspace` (audit generic stripped from `session/**` on the way) |
| `eos-workspace-runtime/src/isolated/audit.rs`, `take_isolated_audit`, audit blocks in outcome metadata, daemon `record_tool_call`/`record_isolated_tool_call`, `isolated_workspace.audit_jsonl_path` config | **dropped, not migrated** |
| `eos-workspace-runtime/src/contract/file_ops.rs` | `eos-file-ops` (DTOs + semantics + `FileBackend`) |
| `eos-workspace-runtime/src/contract/{ids,lease}.rs` | `eos-cas` (`Snapshot`, typed ids) |
| `eos-workspace-runtime/src/contract/{mode,command,mutation,read_view,response}.rs` | dissolved into owning tool crates; numeric helpers stop being exported vocabulary |
| `eos-daemon/src/occ/{mod,service_cache}.rs` | `eos-store` (per-root writer cache becomes `Store`, owned by the daemon composition root; plugins' `occ_callbacks` use the same instance — MF-1 preserved) |
| `eos-daemon/src/overlay/{mod,convert}.rs` (publisher adapter) | `eos-store::publish_capture` |
| `eos-daemon/src/workspace/files/ports.rs` | split: storage halves → `eos-store` / `StoreBackend`; isolated halves → `IsolatedView` in `eos-isolated-workspace`; the `record_isolated_tool_call` audit recorder is dropped |
| `eos-daemon/src/workspace/isolated/{runtime,ns_runner,state}.rs` | `eos-isolated-workspace` (port impl becomes concrete code) |
| `eos-daemon/src/workspace/{run,files,isolated}/ops.rs`, `cancel.rs` | stay in daemon as thin arg-parse → tool-crate-call handlers |
| `eos-workspace-runtime` crate | **deleted** |

Net effect: ~11k LOC reorganized with a real deletion dividend — the duplicate
command pair (~620 LOC), the identical ops wrappers (~110), the port layers and
their daemon implementations (~700+), the whole audit pipeline (~300 LOC across
both crates plus its config and the generic threading), and the contract
grab-bag dissolve.

## 4. Staged execution plan

Each stage compiles, passes `cargo test` for touched crates, and keeps the
listed e2e suites green before the next begins. Wire ops in
`eos-api/contract/ops.json` never change.

| Stage | Work | Verify |
| --- | --- | --- |
| 1 | Create `eos-store`; move daemon `occ/` glue + per-root cache + the storage internals of `EphemeralFilePorts`; daemon re-points. | `eos-occ` contention e2e, direct-file contracts e2e |
| 2 | Move `Snapshot` + typed ids into `eos-cas`; temporary re-exports from old paths. | workspace-wide `cargo check` |
| 3 | Create `eos-command-session` (verbatim module move). | command-session protocol smoke e2e |
| 4 | Create `eos-ephemeral-workspace` (`create/mount_plan/capture/discard`). | ephemeral ops unit tests |
| 5 | Create `eos-isolated-workspace`; absorb daemon ns runtime + `IsolatedFilePorts` view internals; delete `NamespaceRuntimePort`/`LayerStackSnapshotPort`; drop the audit pipeline (`audit.rs`, the `A: AuditSink` generic, `audit_jsonl_path` config) and delete the `audit.jsonl` assertions in `isolated_workspace_lifecycle.rs` (~lines 182-298) in the same change. | isolated lifecycle + cross-mode consistency e2e (lifecycle test updated first — the assertion removal is part of the behavior change, not a cover-up) |
| 6 | Create `eos-command-ops`: port registry/manager, unify the two `command.rs` files into one prepare/settle path, delete `WorkspaceRunHostPorts` and `take_isolated_audit` (isolated settle becomes registry cleanup only); daemon `run/ops.rs` re-points. | command lifecycle, cancel, sweep, isolated command e2e; `isolated_workspace_private_no_publish.rs` keeps asserting via the daemon audit tap that no `occ.publish` occurs (its wire-exposure check for the audit payload becomes trivially true) |
| 7 | Create `eos-file-ops` (`FileBackend` + semantics); daemon `files/ops.rs` re-points; delete `files/ports.rs`. | direct-file contracts + cross-mode consistency e2e |
| 8 | Delete `eos-workspace-runtime` and stage-2 re-exports; full gate. | full e2e suite, workspace `cargo check`/`clippy`/`test` |

## 5. Invariants preserved

- **MF-1**: exactly one OCC writer per root — the per-root cache moves into
  `Store`, constructed once in the daemon; plugin OCC callbacks route through
  the same instance.
- **Isolated never publishes** — now enforced by crate dependency (no
  store/occ/layerstack edge) rather than by a crate that also hosts the
  publish path.
- **Atomic capture-then-publish per ephemeral run** — `capture()` walks only
  the upperdir; `publish_capture` submits one changeset against the run's
  snapshot version with base-hash revalidation, unchanged.
- **Lease/GC barriers** — leases still bracket every overlay mount and every
  isolated session; release stays with whoever acquired (command-ops for
  ephemeral runs, the daemon enter/exit composition for isolated sessions).
- **Single-threaded namespace children** — `eosd ns-holder` / `ns-runner`
  subcommands and the current-exe spawn protocol are untouched; only the
  spawning code's crate changes.
- **Wire protocol** — op names, args, and response envelopes unchanged (the
  isolated audit payload was never wire-exposed, so dropping it changes no
  response shape); the e2e suite is the regression harness. The only external
  surface change is the removal of the `isolated_workspace.audit_jsonl_path`
  config key and the `audit.jsonl` file it pointed at.
- **Daemon transport audit ring unchanged** — `tool_call.started`/
  `tool_call.completed` events and `api.audit.pull` keep working; they are
  dispatch-level and never depended on the dropped workspace audit pipeline.

## 6. Decisions

Resolved 2026-06-11:

1. **Audit: dropped wholesale.** The isolated-workspace JSONL audit pipeline
   is deleted during the migration, not redesigned or carried over. Isolated
   settle is literally "do nothing" (registry cleanup only); isolated file
   writes stop recording tool calls. The daemon's transport-level audit ring
   is a separate subsystem and stays.
2. **`eos-command-session` is its own crate.** It mirrors the existing
   mechanism-crate precedent (`eos-overlay`, `eos-namespace`) and isolates the
   heavy Linux PTY/tokio surface from `eos-command-ops` policy code.

Open:

3. **`eos-cas` as the vocabulary floor.** Reusing it avoids a contract crate
   but stretches the name further (it already hosts runner DTOs). A later
   rename (e.g. `eos-core-types`) is out of scope here.

## 7. Naming review

The storage floor is well-named (`LayerStack`, `MergedView`, `CommitQueue`,
`capture_upperdir` all say exactly what they are). The rot is concentrated in
the middle layer being deleted. Principles for the new crates:

1. **Name the responsibility, not the category.** `runtime`, `Manager`,
   `Ports`, `contract`, generic `Ops` are category words that invite
   grab-bags. (`WorkspaceRunHostPorts` is three category words in a row.)
2. **The relationship must read left-to-right.** A command runs *on* a
   workspace; `WorkspaceRun` inverts that and suggests the workspace runs.
3. **A mode word in a function name is a missing type.**
   `prepare_ephemeral_command` / `prepare_isolated_command` exist because
   `ExecTarget` didn't.
4. **One concept, one name, every layer.** Today one tool is spelled
   `read_command_progress_lines` (agent), `sandbox.command.poll` (catalog),
   `api.v1.command.read_progress` (daemon op), `ReadCommandProgress` /
   `read_progress` (runtime). One snapshot concept is `Lease` (layerstack) and
   `SnapshotLease` (runtime contract).
5. **Name by settle-time behavior, not connotation.** `commit_or_record`
   encodes a hidden mode branch in a verb; `finish_reaped` names the trigger
   instead of the meaning.
6. **Typed ids over `String` keys.** Registry keys and caller ids are raw
   strings today.
7. **Initialisms stay inside the crate that implements them.** Nothing above
   `eos-store`'s implementation should say "occ".

| Current name | Problem | Replacement |
| --- | --- | --- |
| `WorkspaceRun`, `EphemeralRun`, `IsolatedRun` | inverted relationship (rule 2) | `ActiveCommand { session, workspace: BoundWorkspace }`, `enum BoundWorkspace { Ephemeral(EphemeralWorkspace), Isolated(IsolatedBinding) }` |
| `WorkspaceRunManager`, `WorkspaceRunRegistry`, `CallerRuns` | category suffixes + "run" collides with `RunRequest`/`RunMode` | `CommandOps` (public API), `CommandRegistry`, `CallerCommands` |
| `WorkspaceRunHostPorts`, `DaemonRunHostPorts`, `host_ports.rs` | pure category naming on a god-port | deleted outright |
| `finish_reaped` | names the trigger, not the meaning | `settle` (glossary: provision → run → settle) |
| `commit_or_record` | either/or verb hiding the mode branch | `FileBackend::apply` — one verb, backend defines the durable meaning, outcome reports `published` |
| `EphemeralWorkspaceOps` / `IsolatedWorkspaceOps` | identical wrappers named by mode | deleted (the `FileBackend` impls are `StoreBackend`, `IsolatedBackend`) |
| `prepare_/finalize_/discard_ephemeral_command`, `prepare_/finalize_isolated_command` | rule 3 | one `prepare(ExecTarget)` / `settle` path in `eos-command-ops` |
| `IsolatedCommandHandle` | not a handle — an owned copy of binding context, no lifecycle | `CommandBinding` (returned by `IsolatedSessions::binding`) |
| `SnapshotLease` + layerstack `Lease` | two names/types for one concept | one `Snapshot` DTO in `eos-cas`; `Lease::into_snapshot()` at the store boundary |
| `WorkspaceHandleId` | which workspace? only ever isolated | `IsolatedWorkspaceId` |
| `command_session_id: String` | stringly key; also drifts vs "command id" | `CommandId` newtype, serialized as `command_session_id` (wire stable) |
| `EphemeralRunDirs`, `RunDirCleanup` | "run" again | `OverlayDirs`, `OverlayDirsGuard` |
| `LayerStackRoot` newtype vs raw `root: &Path` in ports | one concept, two spellings | `StoreRoot` used consistently across `eos-store` APIs |
| `WorkspaceMode` | flag enum driving behavior branches | deleted (`ExecTarget` / backend types) |
| `contract` module | category name that became a grab-bag | dissolved into owning crates |
| `apply_occ_changeset`, `occ_service_for_root` above the store | rule 7 | `RootStore::{commit_direct, publish_capture}` |
| `read_command_progress_lines` / `poll` / `read_progress` | rule 4 | canonical `read_command_progress` for code symbols; existing wire names kept as catalog aliases |

On `ephemeral` vs `isolated` themselves: both modes are namespace-isolated,
and the "ephemeral" one is the only one that *publishes* — the pair names the
wrong axes (lifetime and isolation) instead of the defining axis (what happens
to the upperdir at settle). More honest names would be commit/transactional
vs private/session. They stay anyway — decision: they are wire-visible product
terms (`sandbox.isolation.*`, `isolated_workspace_id`) and the cost of
re-educating every surface exceeds the gain — but each crate's rustdoc must
lead with the settle semantics, not the connotation: *ephemeral = per-command
overlay transaction whose changes publish on success; isolated = persistent
private overlay whose changes never leave it.*
