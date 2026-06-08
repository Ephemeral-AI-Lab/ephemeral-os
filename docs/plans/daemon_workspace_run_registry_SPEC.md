# Daemon Workspace-Run Registry — Migration SPEC

Status: complete — Phases 1–8 landed. Phase 8 was completed via the full §3.4 migration (not the earlier re-keyed-in-place divergence): the caller-keyed registry + manager were relocated from `eos-command-session` into `eos-daemon/src/services/workspace_run/`, `CommandSession` was reduced to a pure PTY substrate (`reap`/`persist_final`, no policy) with the publish-vs-discard decision lifted into the daemon-side run, `command_session/` + `isolated_workspace/` were merged under `services/workspace_run/`, and `OnceLock<CommandSessionManager>` became the daemon-owned `OnceLock<WorkspaceRunManager>`. Wire-op strings and the `cancel_workspace_runs[_by_caller_id]` coordinator are unchanged. (See Progress tracker below.) A post-landing audit hardened five gaps — most notably a silent timeout-completion loss and a cancel lease leak — and recorded the real spec-vs-impl divergences (§10); a later independent pass re-verified the F1–F5 fixes + named tests and removed migration-orphaned dead code (net −78 LOC); see **§10–§11**.
Owner: sandbox (daemon substrate)
Scope: `sandbox/crates/eos-daemon`, `eos-command-session`,
`eos-ephemeral-workspace`, `eos-isolated-workspace`, `eos-workspace-api`
Related:
- `docs/plans/agent_run_local_background_supervisor_SPEC.md` for agent-core
  agent-run cancellation.
- `docs/plans/backend_server_cancellation_wiring_SPEC.md` for backend-server
  cancellation orchestration.

## 0. Status, rules, and how to verify

### Workspace & command-session rules (the model)

These are the load-bearing rules the registry encodes. An earlier draft of this
spec inverted rule 1 (it claimed "one ephemeral session per caller"); that was
wrong and is corrected throughout.

1. **A caller (== agent run, `caller_id == agent_run_id`) holds *many* ephemeral
   workspaces *or* *one* isolated workspace** — never both. The XOR is enforced by
   the isolated enter/exit gate (enter is rejected while the caller has any live
   command session), not by a per-session cap. There is **no** cap on concurrent
   ephemeral command sessions per caller.
2. **Each ephemeral workspace owns exactly *one* command session** (1:1,
   co-terminal — the workspace is created at `exec_command` and discarded/published
   when that session settles). **The isolated workspace owns *many* command
   sessions** (1:N — they share its one namespace + snapshot).

In types: `runs: HashMap<CallerId, CallerRun>`, where
`CallerRun = Ephemeral(HashMap<CommandSessionId, EphemeralWorkspaceRun>) | Isolated(IsolatedWorkspaceRun)`,
`EphemeralWorkspaceRun { session }` (singular), `IsolatedWorkspaceRun { sessions }` (map).

### Test environment (REQUIRED)

All **behavioral** verification runs the real `eosd` daemon **inside the Docker
`sweevo-dask__dask-10042:latest` (`linux/amd64`) container** via
`sandbox/crates/eos-e2e-test` (`--features e2e`); the host process is only a TCP
client that uploads `eosd` and drives it. macOS-local `cargo test`/`clippy` only
*compile* the Linux paths (`--target x86_64-unknown-linux-musl`) or exercise the
non-Linux unit scaffold — **never** real daemon/PTY/overlay behavior, so they are
not behavioral proof. **Rebuild `eosd` before every E2E run** so the container runs
your changes:
`cargo run -p xtask -- package --target x86_64-unknown-linux-musl` (writes
`sandbox/dist/eosd-linux-amd64`). The suite is flaky under x86-on-arm64 emulation
(~14–19 of 60 fail, varying run-to-run, all in the process/signal/PTY/setsid +
transcript-format set); the bar is *no new registry-correctness failures*, not a
clean run.

### Progress tracker

| Phase | Status |
|---|---|
| 0 — `caller_id` granularity (`== agent_run_id`) | ✅ done |
| 1 — shared value objects (`SnapshotLease`, `Lifecycle`) | ✅ done + committed |
| 2 — reap/publish split (cancel → discard, never publish) | ✅ done + committed |
| 3 — introduce caller-keyed registry | ✅ done + committed (`107eec33b`) |
| 4 — re-home ephemeral (N runs/caller, no cap) | ✅ done + committed (`107eec33b`) |
| 5 — re-home isolated | ✅ done — `active_command_sessions` side-map dropped (`register/unregister_command_session` removed); the caller-keyed command-session registry is the sole owner of isolated sessions, and `ttl_sweep` + the rebind gate consult it. Namespace teardown stays delegated to `IsolatedSession::exit` (divergence). |
| 6 — re-point completion/sweep onto runs | ✅ done (`107eec33b`) |
| 7 — cancel surface (`cancel_all_workspace_runs_by_caller_id` / `_runs`) | ✅ done — `services/workspace_run.rs` coordinator + wire ops `api.v1.cancel_workspace_runs_by_caller_id` and `api.v1.cancel_workspace_runs`; `op_exit` routes through the per-caller coordinator. E2E (real `eosd` in Docker): cancel-by-caller discards owner runs + spares siblings + parks no completion; the **cancel-mid-write test asserts the shared LayerStack `manifest_version` is unchanged and the write stays unpublished** (the OCC-discard invariant); whole-sandbox sweep tears down every caller. |
| 8 — remove flat manager / merge services | ✅ done — full §3.4 migration (supersedes the earlier divergence). `CommandSession` is now a pure PTY substrate (`reap → ReapedCommand`, `persist_final`; no `policy`/`finalized`); the publish-vs-discard decision lives in the daemon-side run (`RunSession{session, policy}`). The caller-keyed registry + manager moved from `eos-command-session` into `eos-daemon/src/services/workspace_run/` (`registry.rs` = `WorkspaceRunRegistry`, `manager.rs` = `WorkspaceRunManager`); `command_session/` + `isolated_workspace/` merged under `workspace_run/` (`commands.rs`, `config.rs`, `wire.rs`, `ports/`, `cancel.rs`, `isolated/`); `OnceLock<CommandSessionManager>` → daemon-owned `OnceLock<WorkspaceRunManager>`. `eos-command-session` is now substrate-only (`CommandSessionCompletion` moved to its `response.rs`). Wire-op strings + the `cancel_workspace_runs[_by_caller_id]` coordinator fns are byte-stable. Verify (real `eosd` rebuilt, Docker): `eos-command-session` E2E **44/63 pass** with all **registry-correctness** tests green — incl. `cancel_workspace_runs_by_caller_id_discards_overlay_writes` (the §9 cancel-never-OCC-publishes invariant on the relocated registry), `collect_completed_drains`, `session_count_accuracy`; the 19 failures are all in the documented process/signal/PTY/setsid/output/parallel/network emulation-flake set (top of the spec's ~14–19/60 baseline). Isolated E2E 16/20 (the 4 failures are PTY-output-timing + netns + ephemeral scratch-visibility in untouched namespace/overlay/runner code). macOS + `x86_64-unknown-linux-musl` clippy clean (`-D warnings`); daemon + `eos-command-session` unit suites green (manager/registry scaffold tests moved with the code). |

**Historical note — the Phases 1–7 divergence (now superseded by Phase 8):**
Phases 1–7 originally landed as a re-keyed-in-place divergence: the caller-keyed
registry lived **in `eos-command-session`** (the re-keyed
`CommandSessionRegistry`/`CommandSessionManager`), the §3.3 policy-ectomy was skipped
(the ephemeral overlay lease/dirs stayed in the session's policy), and the cancel
surface was a thin `eos-daemon/src/services/workspace_run.rs` coordinator rather than
the full §3.4 `workspace_run/` service tree. **Phase 8 reverted this divergence and
completed the full §3.4 migration**: the registry + manager relocated into
`eos-daemon/src/services/workspace_run/`, the policy-ectomy landed (`CommandSession`
is substrate-only; the run owns publish-vs-discard), and `command_session/` +
`isolated_workspace/` merged under `workspace_run/`. The Phase-5 behaviors carry over
unchanged: the caller-keyed registry is the sole owner of command sessions (no
`active_command_sessions` side-map), and the isolated **rebind gate** is keyed on open
isolated callers (consulting `active_command_sessions_for_caller`). Isolated namespace
teardown is still delegated to `IsolatedSession::exit` via `op_exit`, which now routes
through the per-caller `cancel_workspace_runs_by_caller_id` coordinator.

## 1. Why this migration

Today command sessions (PTYs) live in **one flat, daemon-global registry keyed by
session id** (tagged with `caller_id`), and ephemeral workspaces are not first-class
objects at all — they exist only 1:1 with a command session, implicitly. Isolated
workspaces are a *separate* registry. Whole-sandbox operations (cancel-all, the
"no active background work" gate, commit's lease check) must re-derive ownership
from `caller_id` and hand-partition "is this caller in isolated mode?".

Target: the daemon holds **one workspace-run registry keyed by `CallerId`**, and
**each workspace run owns its own command session(s)**:

- **ephemeral workspace run** — owns exactly **one** command session; a caller may
  hold **many** of these (each its own ephemeral workspace)
- **isolated workspace run** — owns **many** command sessions, persistent; a caller
  has **at most one**

A caller is in exactly **one mode** — many ephemeral runs *or* the one isolated run;
the XOR is enforced by the isolated enter/exit gate, not a per-session cap. A single
`HashMap<CallerId, CallerRun>` keys both, where `CallerRun` is an enum holding the
caller's set of ephemeral runs or its lone isolated run. This makes
`cancel_all_workspace_runs_by_caller_id(caller)` a one-call,
self-contained teardown, `cancel_all_workspace_runs` a single iteration, and gives
the lease/enter gates an authoritative source of truth. It is the **prerequisite**
for the clean §3 sandbox-cancel flow in the cancellation spec.

## 2. Current state (verified)

| Thing | Where | Shape |
|---|---|---|
| Global command-session manager | `eos-daemon/.../command_session/mod.rs:56-58` | `static MANAGER: OnceLock<CommandSessionManager>` (singleton) |
| Command-session registry | `eos-command-session/src/registry.rs:32-34` | `sessions: Mutex<HashMap<String, Arc<CommandSession>>>` + `completed` — flat, **keyed by session id**, tagged with `caller_id` |
| `CommandSession` | `eos-command-session/src/session.rs:21` | `{ id, caller_id, command, policy (overlay finalize/publish), process, output/final/transcript paths, cancelled, output_drain_grace_ms, finalized, started_at, timeout }` |
| Per-caller queries | `manager.rs:309` (`count_by_caller`), `:336` (`cleanup_caller`) | derive ownership from `caller_id` (handle multiples today) |
| Completion / reaping | `mod.rs` (`collect_completed`, `push_completed`, `sweep_expired`) | iterate the flat registry; agent-core heartbeat drains |
| Isolated registry | `eos-isolated-workspace/.../session.rs:45` (`IsolatedSession { by_caller, handles, network, scratch_root, … }`) | per `caller_id`; `list_open_callers`, `session.exit`, `reap_orphan_resources` (gc.rs:124) |
| Isolated per-workspace handle | `eos-isolated-workspace/.../session/types.rs:33` (`WorkspaceHandle`) | lease + overlay + `ns_fds`/`holder_pid`/`veth`/`cgroup_path` |
| Isolated ↔ its sessions | isolated daemon state `active_command_sessions: HashMap<id,caller>` (`mod.rs:43`); exit → `cleanup_command_sessions_for_caller` → `command_session_manager().cleanup_caller` | isolated cleans its sessions by calling the **global** manager |
| Command-bound ephemeral workspace | `DaemonEphemeralCommandPort::prepare_context(command_session_id)` → `session_dir = scratch_root/command_session_id` (`ports/ephemeral.rs:40`) | **1:1 with a command session**; daemon creates it at session start |
| Per-op overlays (OUT OF SCOPE) | `EphemeralWorkspaceOps` (`ops/files.rs:39,59,79`), `finalize_publishable_workspace` (`plugins/overlay.rs:169`) | synchronous, per-tool-call, no PTY, torn down inside the op handler |

Key consequences:
- An ephemeral and an isolated command session sit in the **same** global manager,
  distinguished only by whether `caller_id` is in isolated mode.
- The daemon "owns the PTY/process/session registry" deliberately
  (`eos-ephemeral-workspace/.../command_session/types.rs:30`). This migration keeps
  that and **composes** the run structs in the daemon.

## 3. Target model

### 3.1 Daemon workspace state — one caller-keyed map

Replaces `OnceLock<CommandSessionManager>` (the flat session map) **and** folds in
`DaemonIsolatedState` (its `active_command_sessions` side-map is dropped):

```rust
struct WorkspaceRunRegistry {
    runs: HashMap<CallerId, CallerRun>,                    // ONE map — each caller's runs, keyed by caller
    completed: HashMap<CommandSessionId, CompletedEntry>,  // completion queue, drained by the agent-core heartbeat
    layer_stack_root: PathBuf,
    config: CommandSessionConfig,
}
```

A caller maps to **one `CallerRun`**: its set of ephemeral runs *or* its lone
isolated run (§3.2). The isolated enter-gate rejects entering while a caller has any
active command sessions (`mod.rs:69`), so a caller is either ephemeral *or* isolated,
never both — the `CallerRun` enum expresses that directly. Session-targeted ops
resolve via `runs[caller_id]` then match the session id (the wire request carries
`caller_id`).

Unchanged daemon statics: plugin state, OCC cache, audit buffer,
`invocation_registry`, config `RwLock`s.

> **No per-caller cap (corrected).** A non-isolated caller may hold **many**
> concurrent ephemeral command sessions — each is its own ephemeral workspace run
> (1 session : 1 workspace), and they accumulate under the caller's `CallerRun`.
> `exec_command` never rejects a second. (An earlier draft proposed a one-ephemeral-
> session-per-caller cap; that was wrong — agent runs legitimately hold multiple
> ephemeral workspaces, so the cap is a regression and was removed.)

### 3.2 Workspace-run structs

The **1:1 vs 1:N** cardinality is the load-bearing invariant — `session` (singular)
vs `sessions` (map). Shared field groups are extracted and composed; the closed
two-kind set is an **enum** (not a `dyn` trait — the repo's rule: enum for a closed
set, `dyn` only for open/runtime-selected sets).

```rust
// ── eos-workspace-api: shared value objects ──
struct SnapshotLease { lease_id: String, manifest_version: i64,
                       manifest_root_hash: String, layer_paths: Vec<PathBuf> }
struct Lifecycle     { created_at: f64, last_activity: f64 }

// ── eos-daemon: the two run kinds + the enum ──
struct EphemeralWorkspaceRun {           // 1:1
    caller_id: CallerId,
    session: CommandSession,             // exactly ONE (moved in from the flat manager)
    snapshot: SnapshotLease,
    dirs: EphemeralRunDirs,              // run_dir, upperdir, workdir,
    life: Lifecycle,
}

struct IsolatedWorkspaceRun {            // 1:N
    caller_id: CallerId,
    handle_id: WorkspaceHandleId,
    sessions: HashMap<CommandSessionId, CommandSession>,   // MANY (replaces active_command_sessions)
    snapshot: SnapshotLease,
    ns: NamespaceHandle,                 // ns_fds, holder_pid, readiness_fd, control_fd, veth, cgroup_path
    dirs: IsolatedRunDirs,                  // scratch_dir, upperdir, workdir,
    life: Lifecycle,
}

// The caller-keyed value: a caller holds MANY ephemeral runs OR the ONE isolated
// run. The per-run cardinality above (session singular vs sessions map) is the
// load-bearing invariant; this enum carries the per-caller XOR.
enum CallerRun {
    Ephemeral(HashMap<CommandSessionId, EphemeralWorkspaceRun>),  // many 1-session runs
    Isolated(IsolatedWorkspaceRun),                              // the one many-session run
}

impl CallerRun {
    fn command_sessions(&self) -> Vec<&CommandSession>;
    async fn cancel_workspace(&mut self, reason: &str, grace: Option<f64>);   // tear down OWN resources; never OCC-publishes
}
```

Identity is `caller_id` (the map key) — there is no separate `WorkspaceRunId`. The
inner `CommandSession`(s) keep their own `command_session_id` for session-targeted
ops. `CommandSession` stays in `eos-command-session`, re-parented (see §3.3 for the
one substantive change to it).

`exec_command` (non-isolated) → add a new `EphemeralWorkspaceRun` (one session) to
the caller's `CallerRun::Ephemeral` set, creating the set on the first session.
`exec_command` while in isolated mode → insert a session into that caller's
`IsolatedWorkspaceRun.sessions`.

### 3.3 Teardown + the OCC rule (reap/publish split)

**Cancel must DISCARD, never OCC-publish.** Make this *structural*, not a flag check.

Today the cancel path reaps via `CommandSession::try_finalize_process`
(`session.rs:262`), which calls `finalize_with_output` → `policy.finalize_command_workspace`
→ `finalize_publishable_workspace` → **`publish_upperdir_changes` (the OCC merge)**.
That helper publishes **unconditionally** (`finalize.rs:39`); `is_cancelled` only
relabels the status string. So today a cancelled command that reaps within the grace
window **merges its overlay into the shared LayerStack** — exactly what we must avoid.

Fix by **separating substrate from policy**:
- `CommandSession::reap()` (was `try_finalize_process`) only reaps the child and
  **captures** the upperdir delta — it no longer publishes, and no longer holds a
  `policy`. (`policy`/`finalized` fields are removed from `CommandSession`.)
- The **run** decides what to do with the captured delta: **complete → publish**
  (OCC merge), **cancel → discard**. The cancel path simply never calls publish, so
  "cancel never OCC-merges" is enforced by structure.

```
EphemeralWorkspaceRun::cancel_workspace(reason, grace):          // 1 session
  1. session.cancel_process()                  SIGTERM→SIGKILL on pgid; mark cancelled; drain output
  2. session.reap()                            reap child + capture delta — DO NOT publish
  3. discard_overlay(dirs, snapshot)           remove run_dir/upperdir/workdir; release_snapshot(lease)  (NO publish_upperdir_changes)
  // shared LayerStack is persisted only by the request-level commit gate, never by cancel

IsolatedWorkspaceRun::cancel_workspace(reason, grace):           // N sessions (≈ today's session.exit)
  1. for s in sessions.values(): s.cancel_process(); s.reap()      discard each (isolated upperdir is never published, by design)
  2. kill_holder(ns.holder_pid); close ns.{ns_fds, readiness_fd, control_fd}
  3. teardown_veth(ns.veth); cgroup_rmdir(ns.cgroup_path)
  4. release_snapshot(snapshot.lease_id)
  5. discard upperdir + rmtree dirs.scratch_dir
```

**Registry methods own removal** (a run never removes itself from its parent map):

```
WorkspaceRunRegistry::cancel_all_workspace_runs_by_caller_id(caller_id, reason, grace):   // per-caller op = agent-core's one RPC (§7); caller_id == agent_run_id
  if let Some(run) = runs.get_mut(caller_id): run.cancel_workspace(reason, grace); runs.remove(caller_id)

WorkspaceRunRegistry::cancel_all_workspace_runs(reason, grace):
  for run in runs.values_mut(): run.cancel_workspace(reason, grace)
  runs.clear()
  reap_orphan_resources()                  // GC handle-less eos-iws-* veth/cgroup/scratch
  // GATE (assert no leases) + commit_to_workspace live in the cancellation spec §3
```

Normal completion stays as today (reap → **publish** → push completion); only the
cancel path takes the discard branch. The branch key is `is_cancelled()` set by
`cancel_process`.

### 3.4 Resulting file / folder structure

```
sandbox/crates/
├── eos-command-session/src/
│   ├── session.rs            MOD   CommandSession + cancel_process + reap (policy/finalize REMOVED)
│   ├── process/{signal,runner}.rs   KEEP (PTY/pgid substrate)
│   ├── output.rs response.rs request.rs   KEEP
│   ├── manager.rs            DROP  (registry/cancel/cleanup role → eos-daemon)
│   ├── registry.rs           DROP  (flat session map → eos-daemon WorkspaceRunRegistry)
│   └── lib.rs                MOD   (export CommandSession + reap; drop manager/registry)
│
├── eos-ephemeral-workspace/src/
│   ├── types.rs              MOD   keep EphemeralRunDirs; EphemeralSnapshot → SnapshotLease (moves to workspace-api)
│   ├── finalize.rs capture.rs   KEEP  publish path — called by the run on COMPLETE only
│   ├── discard.rs            NEW   discard_overlay() (remove dirs + release lease, no publish)
│   ├── command_session/      DROP  (prepare/finalize/policy folded into the daemon run + finalize/discard helpers)
│   └── ports.rs dirs.rs error.rs timings.rs   KEEP
│
├── eos-isolated-workspace/src/
│   ├── session/types.rs      MOD   WorkspaceHandle → NamespaceHandle + IsolatedDirs (per-caller indexing leaves)
│   ├── session/lifecycle.rs  MOD   enter/exit → run construct + teardown helpers (kill_holder, release_lease, …)
│   ├── session/gc.rs         KEEP  reap_orphan_resources
│   ├── network.rs caps.rs    KEEP
│   ├── session.rs            MOD   IsolatedSession.{by_caller,handles} registry role → eos-daemon; keep teardown
│   └── command_session/      DROP  (isolated command-session finalize/cleanup → the run)
│
├── eos-workspace-api/src/
│   └── lease.rs              NEW   SnapshotLease, Lifecycle (shared value objects)
│
└── eos-daemon/src/
    ├── services/
    │   ├── workspace_run/                NEW  (replaces command_session/ + isolated_workspace/)
    │   │   ├── mod.rs                     NEW  service entry + with_state(WorkspaceRunRegistry)
    │   │   ├── registry.rs                NEW  WorkspaceRunRegistry + WorkspaceRun enum
    │   │   ├── ephemeral.rs               NEW  EphemeralWorkspaceRun + cancel_workspace/complete (composes session + overlay)
    │   │   ├── isolated.rs                NEW  IsolatedWorkspaceRun + cancel_workspace (composes sessions + namespace)
    │   │   ├── cancel.rs                  NEW  cancel_all_workspace_runs_by_caller_id(caller) / cancel_all_workspace_runs
    │   │   ├── completion.rs              NEW  completed queue + sweep_expired (iterate runs)
    │   │   ├── wire.rs                    MOD  (from command_session/wire.rs) op shaping
    │   │   ├── ports/ephemeral.rs         MOD  (from command_session/ports/) DaemonEphemeralCommandPort
    │   │   └── config.rs                  KEEP (from command_session/config.rs)
    │   ├── command_session/               DROP (merged into workspace_run/)
    │   └── isolated_workspace/            DROP (merged into workspace_run/)
    ├── ops/
    │   ├── registry.rs                    MOD  op table → workspace_run handlers
    │   ├── command_sessions.rs            MOD  re-point to workspace_run (op shapes unchanged)
    │   ├── isolated.rs                    MOD  enter/exit → workspace_run (exit = cancel_all_workspace_runs_by_caller_id)
    │   └── checkpoint.rs control.rs       KEEP (commit_to_workspace, op_cancel)
    └── runtime/invocation_registry.rs     KEEP
```

**Ownership rationale:** the run structs + registry live in `eos-daemon` because a
run *composes* a `CommandSession` (eos-command-session) with overlay
(eos-ephemeral-workspace) / namespace (eos-isolated-workspace) pieces — homing them
here avoids new `workspace-crate → eos-command-session` dependency edges and matches
the existing "daemon owns the PTY registry" intent. The workspace crates stay pure
overlay/namespace logic, invoked by the daemon's run methods. `eos-command-session`
shrinks to the PTY substrate.

### Carve-out (explicitly NOT migrated)

Per-op overlays (`ops/files.rs`, `plugins/overlay.rs`) are **not** workspace runs —
no PTY, no lifetime beyond the synchronous op. They keep `EphemeralWorkspaceOps` /
`finalize_publishable_workspace` as-is and never enter the registry. Interrupting
them, if ever needed, is `op_cancel` at the invocation level.

## 4. Migration approach

**Option B — re-parent + re-key (recommended).** Keep the `CommandSession` substrate
and its reaping/signalling in `eos-command-session`; replace the flat
`CommandSessionRegistry` with the caller-keyed `WorkspaceRunRegistry`. Daemon-wide
concerns (reap, completion, count, cleanup, enter gate) operate on runs. A re-homing
of ownership + the reap/publish split, **not** a rewrite of the PTY lifecycle.

**Option C — full per-workspace substrate.** Move the completion queue and reaper
into each run. Cleanest encapsulation, but relocates the central reaper/completion
plumbing the agent-core heartbeat drains. Higher risk; not recommended unless B
proves insufficient.

The rest of this spec assumes **Option B**.

## 5. Changes by area

### 5.1 Create

| Item | Home | Purpose |
|---|---|---|
| `SnapshotLease`, `Lifecycle` | `eos-workspace-api` | shared value objects composed by both run kinds |
| `enum WorkspaceRun` + `teardown` / `command_sessions` | `eos-daemon` | the closed two-kind set (enum dispatch) |
| `EphemeralWorkspaceRun` (1 session + overlay + lease) | `eos-daemon` (composes `eos-command-session` + `eos-ephemeral-workspace`) | promote the command-bound ephemeral workspace to a first-class run |
| `IsolatedWorkspaceRun` (N sessions + namespace + lease) | `eos-daemon` (composes `eos-command-session` + `eos-isolated-workspace`) | wrap the per-caller isolated handle + its sessions |
| `WorkspaceRunRegistry { runs, completed, … }` | `eos-daemon` | the single caller-keyed registry |
| `cancel_all_workspace_runs_by_caller_id(caller)` / `cancel_all_workspace_runs` | `eos-daemon` | per-caller op (agent-core's one RPC) + the whole-sandbox gate |
| `discard_overlay()` | `eos-ephemeral-workspace` | release lease + remove dirs, no publish (the cancel branch) |

### 5.2 Re-home / change

| Current | Becomes |
|---|---|
| `CommandSessionRegistry.sessions` (flat map) | `runs: HashMap<CallerId, WorkspaceRun>`; sessions owned by their run |
| `CommandSession.{policy, finalized}` + `try_finalize_process` (reap+publish) | `CommandSession::reap` (reap + capture, no publish); publish/discard decided by the run (§3.3) |
| `count_by_caller(caller_id)` | `runs.get(caller).map(\|r\| r.command_sessions().len())` (drives the enter gate) |
| `cleanup_caller(caller_id)` | `cancel_all_workspace_runs_by_caller_id(caller)` |
| `collect_completed` / `push_completed` / `sweep_expired` | iterate `runs` → each run's sessions (completion queue stays daemon-level, Option B) |
| isolated `active_command_sessions` + `cleanup_command_sessions_for_caller` | `IsolatedWorkspaceRun.sessions` owned directly (no call back into a global manager) |
| `exec_command` handler | resolve-or-create the caller's `CallerRun`; ephemeral → add a new 1-session run; isolated → insert session |

### 5.3 Drop

| Item | Why |
|---|---|
| `static MANAGER: OnceLock<CommandSessionManager>` + `CommandSessionRegistry` | replaced by `WorkspaceRunRegistry` |
| `CommandSession.policy` coupling + flat session-id keying + caller-mode partition | publish/discard moves to the run; ownership is explicit per caller |
| `DaemonIsolatedState.active_command_sessions` side-map | isolated run owns its sessions |

### 5.4 Wire-op impact (shapes preserved)

| Op | Resolution under the registry |
|---|---|
| `op_exec_command` | resolve-or-create `runs[caller]`; ephemeral → add a new 1-session run; isolated → insert a session |
| `op_command_write_stdin` / `op_command_read_progress` / `op_command_cancel` | `runs[caller]` → the session matching `command_session_id` |
| `op_command_collect_completed` | drain `completed` (by caller) |
| `op_command_session_count` | N (count of the caller's command sessions, ephemeral or isolated) — feeds the enter gate |
| `op_enter` (isolated) | reject if the caller has any live command sessions |
| `op_exit` (isolated) | `cancel_all_workspace_runs_by_caller_id(caller)` |

## 6. Invariants to preserve

- **One `CallerRun` per caller** (`HashMap<CallerId, CallerRun>`): many ephemeral
  runs (each = 1 session, 1 workspace) **or** the one isolated run (N sessions). The
  ephemeral-vs-isolated XOR is structural and enforced by the isolated enter/exit
  gate; there is **no** per-caller ephemeral-session cap.
- **A command session belongs to exactly one run** — removes the
  ephemeral-vs-isolated `caller_id` partition entirely.
- **Substrate vs policy split**: `CommandSession` reaps; the run publishes (complete)
  or discards (cancel).
- **Cancel discards, never OCC-publishes** (§3.3): the cancel path never reaches
  `publish_upperdir_changes`; the shared LayerStack is persisted on cancel solely by
  the request-level `commit_to_workspace` gate.
- **A run never removes itself from the registry** — registry methods do (§3.3).
- **Central reaping/signalling unchanged** (Option B): `CommandSession` keeps the
  pgid/process and SIGTERM→SIGKILL.
- **Completion delivery unchanged**: the heartbeat still drains `completed`.
- **Per-op overlays untouched** (carve-out §3.4).

## 7. Agent-core cancel integration

> **Prerequisite — VERIFIED: `caller_id == agent_run_id`.** The shared sandbox tool
> helper `request_base(ctx, …)` sets `caller_id = ctx.require_agent_run_id()`
> (`eos-tools/src/tools/sandbox/lib.rs:34-44`), and isolated enter/exit pass
> `agent_run_id` directly (`enter_isolated_workspace.rs:55`,
> `exit_isolated_workspace.rs:60`). So `caller_id` is **per-agent-run**: each run
> (root, subagent) has its own caller, and cancelling one run cancels exactly its own
> workspace run — never a sibling's. The one-RPC-per-caller design below is sound.

> **Daemon wire ops — LANDED.** The two primitives are served by the daemon and
> agent-core binds to these exact strings:
> - per-caller: `api.v1.cancel_workspace_runs_by_caller_id` (const
>   `API_V1_CANCEL_WORKSPACE_RUNS_BY_CALLER`), args `{caller_id (required), grace_s?}`
>   → `{success, caller_id, cancelled_command_sessions, isolated_exited}`.
> - whole-sandbox: `api.v1.cancel_workspace_runs` (const `API_V1_CANCEL_WORKSPACE_RUNS`),
>   args `{grace_s?}` → `{success, cancelled_command_sessions, isolated_callers_exited}`.
>
> Both live in `eos-daemon/src/services/workspace_run.rs` (coordinator) +
> `ops/workspace_run.rs` (handlers). The per-caller op composes the caller's
> command-session discard (`cleanup_command_sessions_for_caller`) with its isolated
> exit-if-open (`exit_isolated`); `op_exit` routes through the same coordinator.

Two cancellation layers use the daemon primitives; command-session teardown collapses
to **one RPC per agent run**, and the sandbox stage adds a request-level backstop.

```
LAYER 1 — agent-core, per agent run        (agent_run_local_background_supervisor_SPEC)
  cancel_agent_run(run):
    1. stop.request()                                       stop the loop
    2. foreground executor abort_all()                      in-flight exec_command/write_stdin FUTURES dropped
    3. cancel children                                      subagents → cancel_agent_run ; workflows → cancel_workflow
    4. cancel_all_workspace_runs_by_caller_id(agent_run_id) ← ONE daemon RPC: kills this caller's PTY(s) + tears down its run
    5. finish records

LAYER 2 — sandbox stage, per request       (backend_server_cancellation_wiring_SPEC + cancellation §3)
  cancel_all_workspace_runs()                               ← GATE/backstop: sweep leftovers, then reap_orphan + GATE + commit
```

- **Foreground tools die like normal tools — but only their agent-core *future*.**
  Aborting the in-flight `exec_command`/`write_stdin` future (step 2) just stops the
  agent from *waiting*; it does **not** kill the daemon-side PTY. The PTY for a
  foreground command lives in the daemon exactly like a backgrounded one. The
  authoritative kill for **both** fg and bg is step 4's single
  `cancel_all_workspace_runs_by_caller_id`. So there is no separate fg/bg command-session
  teardown path.
- **Background command-session cancellation is trivial in agent-core** — no
  per-session enumeration; they are just entries in the caller's run, torn down by the
  one call. **Command sessions leave the agent-core background-supervisor's *cancel*
  responsibility entirely** (the supervisor keeps subagents + workflows). *Completion
  delivery* still flows daemon→`completed`→heartbeat (routed by `caller_id`); if you
  want command sessions gone from agent-core completely, route completions by
  `caller_id` and drop the supervisor command-session category (clean follow-on).
- **The sandbox gate is defense-in-depth.** After the Layer-1 recursion has cancelled
  each caller's run, `cancel_all_workspace_runs()` sweeps any run whose per-caller
  cancel failed or was never reached (e.g., an agent run that errored before step 4),
  **then** `reap_orphan_resources` + the lease-gated `commit_to_workspace`. The sandbox
  owns its own cleanup; it does not trust agent-core finished.

## 8. Migration phases & verification

0. **`caller_id` granularity — DONE.** Verified `caller_id == agent_run_id`
   (`eos-tools/src/tools/sandbox/lib.rs:34-44`; isolated enter/exit pass
   `agent_run_id`). The §7 one-RPC-per-caller design is confirmed.
1. **Shared value objects.** Add `SnapshotLease`, `Lifecycle` to `eos-workspace-api`;
   point `EphemeralSnapshot`/isolated lease fields at them. Verify:
   `cargo check -p eos-workspace-api -p eos-ephemeral-workspace -p eos-isolated-workspace`.
2. **Reap/publish split.** `CommandSession::reap` reaps + captures (no publish);
   remove `policy`/`finalized`; route normal completion's publish through the caller.
   Verify: `cargo test -p eos-command-session` + a cancel-mid-write test asserting the
   shared LayerStack manifest is unchanged.
3. **Introduce the registry (behind the flat manager).** Add `WorkspaceRun` enum,
   `EphemeralWorkspaceRun`, `IsolatedWorkspaceRun`, `WorkspaceRunRegistry` in a new
   `services/workspace_run/`. Verify: `cargo check -p eos-daemon --all-targets`.
4. **Re-home ephemeral.** `exec_command` (non-isolated) adds a new 1-session
   ephemeral run to the caller's set (no cap); route
   `write_stdin`/`read_progress`/`cancel`/`count` through it. Verify: command-session
   matrix E2E.
5. **Re-home isolated.** `IsolatedWorkspaceRun` owns its sessions; drop
   `active_command_sessions` + `cleanup_command_sessions_for_caller`; `op_exit` =
   `cancel_all_workspace_runs_by_caller_id(caller)`. Verify: isolated lifecycle + enter-gate E2E.
6. **Re-point daemon concerns.** completion/sweep/heartbeat iterate `runs`. Verify:
   completion-delivery + backpressure E2E.
7. **Cancel surface.** `cancel_all_workspace_runs_by_caller_id` / `cancel_all_workspace_runs` (teardown
   in the run, removal in the registry). Verify: cancel-all E2E (no live sessions /
   leases) + the cancel-mid-write manifest-unchanged E2E.
8. **Remove the flat registry + merge services.** Delete
   `OnceLock<CommandSessionManager>` and `command_session/`/`isolated_workspace/`
   service modules (or leave thin re-pointing shims one release). Verify:
   `cargo clippy -p eos-daemon -p eos-command-session --all-targets -- -D warnings` +
   full `eos-command-session` + isolated E2E suites.

### Success criteria

- The daemon holds one `HashMap<CallerId, CallerRun>`; every command session is
  owned by exactly one workspace run (ephemeral run = 1 session, isolated run = N),
  and a caller holds many ephemeral runs or the one isolated run.
- All existing command-session and isolated wire ops behave identically (same E2E
  results), routed through the registry.
- A cancelled command never OCC-merges (cancel-mid-write manifest test passes).
- `cancel_all_workspace_runs` tears down every run with no `caller_id` partition logic.
- Per-op overlays are unchanged and never registered.

## 9. Risks & open questions

- **`caller_id` granularity — RESOLVED.** Verified `caller_id == agent_run_id`
  (`eos-tools/src/tools/sandbox/lib.rs:34-44`; isolated enter/exit pass `agent_run_id`),
  so the §7 one-RPC-per-caller integration is sound (cancelling one agent run tears
  down exactly its own workspace runs, never a sibling's).
- **Finalize split (OCC merge).** Removing `policy`/`finalize` from `CommandSession`
  is the highest-churn change (its tests assume the session finalizes). Risk: a
  missed branch silently merges a cancelled command's writes. Cover with the
  cancel-mid-write manifest-unchanged test.
- **Completion queue placement (Option B vs C).** B keeps `completed` daemon-level
  (sourced by iterating runs); confirm delivery order + reported-once survive.
- **Isolated multi-session teardown ordering.** Cancel all sessions before
  namespace/lease teardown — `session.exit` already does this; confirm parity when
  sessions are owned directly.
- **Sweep/TTL reaper** must expire an ephemeral run (its one session) and individual
  isolated sessions without tearing down the persistent isolated run.
- **Service merge churn.** Merging `command_session/` + `isolated_workspace/` into
  `workspace_run/` is sizable; the shim option (phase 8) de-risks it.

## 10. Post-landing audit & hardening (2026-06-08)

A read-only audit (parallel subagents + adversarial invariant traces) verified the
landed migration against §3/§6/§7 and then hardened five gaps. The core model holds:
`CommandSession` is substrate-only (`reap → ReapedCommand`, no policy), the daemon owns
the caller-keyed registry, cancel routes structurally to discard, and agent-core drives
`api.v1.cancel_workspace_runs_by_caller_id`. The §3.2/§3.4 struct/layout and the Phase-1
`Lifecycle` value object never matched the prose (acknowledged below); they are harmless.

### Verified invariants (unchanged code)

- **Cancel never OCC-publishes** (§3.3/§6): every reap→settle path keys discard on the
  kill flag; `settle_isolated` is audit-only; start-time failures release lease + dirs
  without publishing. Confirmed by E2E `cancel_workspace_runs_by_caller_id_discards_overlay_writes`.
- **Caller-keyed XOR + central reaper + completion drain + agent-core wiring**: hold.

### Gaps fixed

| # | Severity | Gap | Fix |
|---|---|---|---|
| F1 | high | Per-caller/whole-sandbox cancel only removed a run when `reap()` succeeded, so a child the grace could not reap (SIGKILL-immune D-state) leaked its `CallerRun` entry **and** its snapshot lease — defeating the Layer-2 assert-no-leases gate (§3.3 mandates unconditional removal). | `manager.rs`: added `force_discard` — after the drain, un-reaped runs are removed from the registry and (ephemeral) their lease + dirs released, reap-independently. Best-effort dirs (orphan-reaper backstop); the lease is the gate-relevant resource. |
| F2 | medium | `CallerRun` was a degenerate enum whose two arms were identical maps; `insert` silently **dropped** a freshly spawned PTY on a (post-F1-impossible) variant mismatch — orphaning the process. | Collapsed `CallerRun` → a `CallerRuns(HashMap)` newtype; `insert` is now total. Net-negative, removes the bug class (XOR stays enforced by the enter gate; each `WorkspaceRun` is self-describing). |
| F3 | high | `sweep_expired` killed a past-deadline session via `cancel_process` (cancel flag) → `publish_completion = !cancelled` → **no completion parked**. A fire-and-forget command that hit its timeout was removed silently, leaving its agent-core background session stuck `Running` forever; a `bool` could not distinguish a timeout kill from a user cancel across sweep ticks. | Replaced `CommandSession.cancelled: bool` with `kill: Option<KillReason>` (`Cancelled` → "cancelled"/130, `TimedOut` → "timed_out"/124). New `time_out_process` records `TimedOut` (a user cancel still wins). The sweep now parks a completion unless the kill was a user cancel; discard stays keyed on `kill.is_some()` (both reasons discard — cancel-never-publishes preserved). No kill-*timing* change. New host-runnable unit test locks the status mapping. |
| F4 | low | `CommandSessionCompletion.notification_result` + the `result = notification_result` swap were dead surface — every producer wrote it equal to `result`, no consumer (agent-core or wire client) read it; a latent footgun if they ever diverged. | Removed the field, the swap, and its wire serialization across all producers. Confirmed by E2E `collect_completed_drains`. |
| F5 | low | The completed-queue eviction comment under-described the daemon-global (cross-caller), silent drop. | Comment now explicitly owns the cross-caller eviction; no log line added (eos-daemon has no log surface — matching existing style over adding a `tracing` dep). |

### Verification

musl `cargo check --all-targets` + `clippy -D warnings` clean (the Linux-gated floor;
macOS does not compile the core). Host unit tests pass; a new unit test locks the F3
`KillReason → status` map. E2E against a rebuilt `eosd` in the Docker
`sweevo-dask__dask-10042` container, all **registry-correctness** tests pass:

- F1 / cancel-never-publishes: the three `cancel_workspace_runs*` tests, incl.
  `cancel_workspace_runs_by_caller_id_discards_overlay_writes` (the §9 manifest-unchanged
  invariant).
- **F3 end-to-end**: a new `background_timeout_parks_collectable_completion` —
  a backgrounded, unpolled command that hits its timeout is reaped by the *sweep* and parks
  a collectable completion (this exercises `sweep_expired → time_out_process → finish_reaped →
  push_completed → collect`; before F3 it was dropped silently). `exec_timeout` covers the
  separate *foreground/runner* timeout path (it reaps inline via `wait_for_yield`, `kill == None`)
  — confirming that path is unbroken, not F3's sweep path.
- F4 `collect_completed_drains`; F2 `session_count_accuracy`.

The one failure, `ctrl_c_char_cancels_command_session`, is a pre-existing qemu timing flake
(the cancel exceeds `cancel_wait_ms=500ms` under x86-on-arm64 emulation and returns the inline
`CommandResponse::cancelled` (`exit_code: null`) path — code untouched by this audit), in the
documented process/signal/PTY flake set.

### Acknowledged divergences (left as-is) & deferred cross-spec findings

- **§3.2/§3.4 prose vs impl**: run logic lives in `manager.rs` (no `ephemeral.rs`/`isolated.rs`/
  `completion.rs`/`ports/`); the isolated run is N single-session `IsolatedRun` entries (namespace
  owned by `IsolatedSession`, torn down by `exit_isolated`), not one `IsolatedWorkspaceRun{sessions}`;
  the Phase-1 `Lifecycle` value object was never created. All functionally harmless.
- **`EphemeralSnapshot = SnapshotLease` alias** and **`PreparedCommandWorkspace.session_dir`**
  (only a test reads it): transitional churn, left as-is (broad rename / no production cost).
- **Deferred — agent-core delivery semantics (owned by `agent_run_local_background_supervisor_SPEC`,
  dirty worktree)**: (1) daemon `read_progress` on a finalized session returns the terminal result
  via a non-removing `completed_result`, so the heartbeat re-delivers it — the same completion is
  surfaced to the model twice; (2) a per-session Ctrl-C/cancel on an already-parked session removes
  the completion inline but the agent-core tool never flips the background session off `Running`.
  Both fixes belong in the agent-core background supervisor, not this daemon spec.

## 11. Independent re-verification & dead-code cleanup (2026-06-08)

A second, independent pass re-checked the §10 audit's *claims* (not just the code) and then
removed migration-orphaned dead code. The model and the F1–F5 fixes hold exactly as §10 records.

### Re-verified (no change)

- **F1–F5 present and correct at file:line**: `force_discard` (reap-independent removal + lease/dir
  release), the `CallerRuns(HashMap)` total-insert newtype, `kill: Option<KillReason>` with
  timeout-parks-completion, `notification_result` fully gone, the eviction comment.
- **Named tests exist and assert the claimed invariants** (read, not just grepped):
  `cancel_workspace_runs_by_caller_id_discards_overlay_writes` asserts the shared `manifest_version`
  is **unchanged** *and* the file is unpublished; `background_timeout_parks_collectable_completion`
  asserts a timeout parks a single collectable (non-redelivered) completion; the F3 host unit test
  `kill_reason_maps_to_terminal_status` locks `Cancelled→cancelled/130`, `TimedOut→timed_out/124`;
  `collect_completed_drains` confirms drain-and-remove.
- **Floor**: `cargo check --all-targets` + `clippy -D warnings` clean on `x86_64-unknown-linux-musl`.

### Dead code removed (net −78 LOC; behavior-neutral, compiler + host-test verified)

| Item | Home | Why dead |
|---|---|---|
| `WorkspaceRunManager::release_lease` inherent method | `eos-daemon` `manager.rs` | byte-identical duplicate of the module free `release_lease`; 2 callers redirected to the free fn |
| `CommandSession::elapsed_s` (`cfg(not linux)`) | `eos-command-session` | zero callers on any target/test/trait/serde path |
| 7 `EphemeralWorkspaceError` variants (`InvalidArgument`, `SnapshotAcquire`, `LeaseRelease`, `RunnerFailed`, `CleanupFailed`, `Io`, `Serde`) + the dead `ephemeral_daemon_error` match arms | `eos-ephemeral-workspace` / `eos-daemon` | never constructed — orphaned when lease/runner/cleanup orchestration moved to the daemon run; `ephemeral_daemon_error` collapses to the `OverlayPipeline` catch-all (behavior-identical) |
| `EphemeralTimings` fields `lease_acquire_s`/`runner_s`/`capture_s`/`cleanup_s`/`total_s`/`extra` + `new`/`insert_extra` | `eos-ephemeral-workspace` `timings.rs` | write-only/never-written; only `publish_s` is read (the OCC-timing fallback), kept |
| `FinalizeRequest.command_started_at` + its `insert_extra` block | `eos-ephemeral-workspace` `finalize.rs` | `None` at both call sites — fully dead plumbing |
| `IsolatedNetwork::initialized()` public getter | `eos-isolated-workspace` `network.rs` | zero callers (the `initialized` field is still read internally at the `install_veth` guard, so only the getter is removed) — pre-existing dead surface, swept here since the crate is in scope |

The sweep removed dead code in the in-scope crates regardless of whether it was orphaned by *this*
registry migration or the earlier crate-boundary refactor; all removals are zero-referrer and
behavior-neutral.

Verification: musl `check --all-targets` + `clippy -D warnings` clean; host `check` (compiles the
`cfg(not linux)` paths) clean; host unit suites green (`eos-command-session`, `eos-ephemeral-workspace`
finalize/command, `eos-workspace-api`, the daemon `workspace_run` registry scaffold); whole-repo grep
confirms zero remaining referrers to any removed symbol. No behavioral path changed, so the §8/§10 E2E
results stand without a rebuild (the only live timing surface, `publish_s`, is unchanged on the wire).

### Left as-is (deliberate)

- All §10 "Acknowledged divergences" above (file-split, `EphemeralSnapshot` alias, `Lifecycle`,
  `session_dir`) — prose-conformance churn, no production cost.
- `test_runtime_stub_enabled()` vs inline `env_true(TEST_HARNESS_ENV)` — not a duplicate; the former is
  the named domain wrapper (7 call sites), the latter a shared env primitive.
