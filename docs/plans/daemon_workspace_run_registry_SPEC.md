# Daemon Workspace-Run Registry — Migration SPEC

Status: proposed / discussion
Owner: sandbox (daemon substrate)
Scope: `sandbox/crates/eos-daemon`, `eos-command-session`,
`eos-ephemeral-workspace`, `eos-isolated-workspace`
Related: `docs/plans/uniform_recursive_cancellation_SPEC.md` (§3 sandbox
finalization depends on this migration)

## 1. Why this migration

Today command sessions (PTYs) live in **one flat, daemon-global registry keyed by
`caller_id`**, and ephemeral workspaces are not first-class objects at all — they
exist only 1:1 with a command session, implicitly. Isolated workspaces are a
*separate* registry. This makes whole-sandbox operations (cancel-all, the
"no active background work" gate, commit's lease check) awkward: they must
re-derive ownership from `caller_id` and partition "is this caller in isolated
mode?" by hand.

Target: the daemon holds **two explicit workspace-run registries**, and **each
workspace run owns its own command session(s)**:

- **ephemeral workspace run** — owns exactly **one** command session (1:1)
- **isolated workspace run** — owns **many** command sessions (1:N), persistent

This makes `cancel_workspace_run(ws)` self-contained (no `caller_id` partition),
gives `cancel_all_workspace_runs` a trivial iteration, and gives the lease gate /
enter gate an authoritative source of truth.

This migration is the **prerequisite** for the clean §3 sandbox-cancel flow in the
cancellation spec.

## 2. Current state (verified)

| Thing | Where | Shape |
|---|---|---|
| Global command-session manager | `eos-daemon/.../command_session/mod.rs:56-58` | `static MANAGER: OnceLock<CommandSessionManager>` (singleton) |
| Command-session registry | `eos-command-session/src/registry.rs:32-34` | `sessions: Mutex<HashMap<String, Arc<CommandSession>>>` + `completed` — flat, keyed by session id, tagged with `caller_id` |
| Per-caller queries | `manager.rs:309` (`count_by_caller`), `manager.rs` (`cleanup_caller`) | derive ownership from `caller_id` |
| Completion / reaping | `mod.rs` (`collect_completed`, `push_completed`, `sweep_expired`) | iterate the flat registry; agent-core heartbeat drains |
| Isolated registry | `eos-isolated-workspace/.../session.rs` (`IsolatedSession { by_caller, handles }`) | per `caller_id`; `list_open_callers`, `session.exit`, `reap_orphan_resources` (gc.rs:124) |
| Isolated ↔ its sessions | isolated daemon state `active_command_sessions`; exit → `cleanup_command_sessions_for_caller` → `command_session_manager().cleanup_caller(caller_id)` | isolated cleans its sessions by calling the **global** manager |
| Command-bound ephemeral workspace | `DaemonEphemeralCommandPort::prepare_context(command_session_id)` → `session_dir = scratch_root/command_session_id` (`ports/ephemeral.rs:40-44`) | **1:1 with a command session**; daemon creates it at session start |
| Per-op overlays (OUT OF SCOPE) | `EphemeralWorkspaceOps` (`ops/files.rs:39,59,79`), `finalize_publishable_workspace` (`plugins/overlay.rs:169`) | synchronous, per-tool-call, no PTY, torn down inside the op handler |

Key consequences of the current shape:
- An ephemeral command session and an isolated command session sit in the **same**
  global manager, distinguished only by whether the `caller_id` is in isolated mode.
- The daemon "owns the PTY/process/session registry" deliberately
  (`eos-ephemeral-workspace/.../command_session/types.rs:30`) — central reaping and
  signalling. This migration keeps that, but re-parents the session objects.

## 3. Target model

### 3.1 Daemon workspace state

Replaces `OnceLock<CommandSessionManager>` (the flat `registry.sessions` map) **and**
folds in `DaemonIsolatedState` (whose `active_command_sessions` side-map is dropped):

```rust
struct DaemonWorkspaceState {
    layer_stack_root: PathBuf,                                    // kept
    config: CommandSessionConfig,                                // kept (from CommandSessionManager)
    ephemeral: HashMap<CommandSessionId, EphemeralWorkspaceRun>,  // ← replaces the flat command-session registry (1 session each)
    isolated:  HashMap<CallerId, IsolatedWorkspaceRun>,          // ← replaces IsolatedSession.{handles,by_caller} + active_command_sessions
    completed: HashMap<CommandSessionId, CompletedEntry>,         // completion queue retained (Option B)
}
```

Unchanged statics: plugin state, OCC cache, audit buffer, `invocation_registry`,
config `RwLock`s.

### 3.2 Workspace-run structs

The **1:1 vs 1:N** cardinality is the load-bearing invariant — it shows up as
`session` (singular) vs `sessions` (map).

```rust
// = today's value record EphemeralWorkspace (types.rs:36) + the owned CommandSession
struct EphemeralWorkspaceRun {
    id: CommandSessionId,            // = run id (1:1)
    caller_id: CallerId,
    invocation_id: InvocationId,
    session: CommandSession,         // ← MOVED IN from the flat manager — exactly ONE
    snapshot: EphemeralSnapshot,     // lease_id, manifest_version, manifest_root_hash, layer_paths
    dirs: EphemeralRunDirs,          // run_dir, upperdir, workdir, output/final/result paths
    created_at: f64, last_activity: f64,
}

// = today's WorkspaceHandle (session/types.rs:33) + its N sessions
struct IsolatedWorkspaceRun {
    handle_id: WorkspaceHandleId,
    caller_id: CallerId,
    sessions: HashMap<CommandSessionId, CommandSession>,   // ← MOVED IN — MANY; replaces active_command_sessions
    lease_id: String, manifest_version: i64, manifest_root_hash: String, layer_paths: Vec<PathBuf>,
    workspace_root: String, scratch_dir: PathBuf, upperdir: PathBuf, workdir: PathBuf,
    ns_fds: HashMap<String, i32>, holder_pid: i32, readiness_fd: i32, control_fd: i32,
    veth: Option<VethAllocation>, cgroup_path: Option<PathBuf>,
    created_at: f64, last_activity: f64,
}

trait WorkspaceRun {
    fn id(&self) -> WorkspaceRunId;
    fn command_sessions(&self) -> Vec<&CommandSession>;
    async fn cancel_workspace_run(&mut self, reason: &str);
}
```

- `CommandSession` (`{ id, caller_id, command, policy, process, output_path,
  final_path, transcript_path, cancelled, output_drain_grace_ms, finalized,
  started_at, timeout }`) **stays in `eos-command-session` unchanged** — it still owns
  the PTY/process/pgid and the signal/reap logic. It is only **re-parented**.
- A new `exec_command` (non-isolated) ⇒ a new `EphemeralWorkspaceRun`; an
  `exec_command` while in isolated mode ⇒ a session inserted into that caller's
  `IsolatedWorkspaceRun.sessions`.
- `WorkspaceRunId`: ephemeral = its `command_session_id` (1:1); isolated = `caller_id`.

### 3.3 `cancel_workspace_run` — and the OCC rule

**Cancel must DISCARD, never OCC-publish.** This is a deliberate behavior change.
Today the cancel path reaps the process via `try_finalize_process`
(`session.rs:262`), which calls `finalize_with_output` → `policy.finalize_command_workspace`
→ `finalize_publishable_workspace` → **`publish_upperdir_changes` (the OCC merge)**.
That free function publishes **unconditionally** (`finalize.rs:39` — no success/cancel
gate); the `cancelled` flag only changes the reported *status string*, not whether the
merge happens. So today, **a cancelled command whose process reaps within the grace
window merges its overlay into the shared LayerStack through OCC** — exactly what we do
not want on cancel. (Only the "still running after grace" arm,
`manager.rs:304`, skips it.)

`EphemeralWorkspaceRun::cancel_workspace_run` therefore takes the **discard** path
(the `cleanup_workspace` / `Drop` semantics at `command_session/policy.rs:69` and
`:116`) and must **not** call `finalize_command_workspace` / `publish_upperdir_changes`:

```
EphemeralWorkspaceRun::cancel_workspace_run(reason):           // 1 session
  1. session.cancel_process()                  SIGTERM→SIGKILL on pgid; set cancelled; drain output
  2. reap WITHOUT publishing                    reap the child; DO NOT call session.finalize() / finalize_command_workspace
  3. DISCARD overlay                            remove dirs.run_dir / upperdir / workdir  (no capture, NO publish_upperdir_changes)
  4. release_snapshot(snapshot.lease_id)        release the layer-stack lease
  5. daemon removes self from state.ephemeral
  // NET EFFECT: no OCC merge. The shared LayerStack is persisted only by the
  // request-level commit_to_workspace gate (cancellation spec §3), not by cancel.
```

`IsolatedWorkspaceRun::cancel_workspace_run` ≈ today's `session.exit`, now iterating
owned sessions. Isolated upperdirs are **already** never OCC-published
(`WorkspaceHandle.upperdir` doc: "DISCARDED on exit — never published"), so isolated
cancel never had the OCC-merge risk:

```
IsolatedWorkspaceRun::cancel_workspace_run(reason):           // N sessions
  1. for s in sessions.values(): s.cancel_process()           SIGTERM→SIGKILL each (discard, no publish)
  2. kill_holder(holder_pid); close ns_fds / readiness_fd / control_fd
  3. network.teardown_veth(veth); remove cgroup_path          (kill cgroup pids + rmdir)
  4. release_snapshot(lease_id)
  5. DISCARD upperdir + rmtree scratch_dir                    (never published, by design)
  6. daemon removes self from state.isolated
```

```
cancel_all_workspace_runs(reason):
  for ws in state.ephemeral.values_mut(): ws.cancel_workspace_run(reason)
  for ws in state.isolated.values_mut():  ws.cancel_workspace_run(reason)
  reap_orphan_resources()                  // GC handle-less eos-iws-* veth/cgroup/scratch
  // GATE + commit_to_workspace live in the cancellation spec §3
```

**Behavior-change call-out for the migration:** the reap-and-finalize path
(`try_finalize_process`) must split into (a) **complete** (process exited normally,
not cancelled) → finalize + OCC publish, as today; and (b) **cancel** → reap +
discard, **no** publish. The `is_cancelled()` flag (already set by `cancel_process`)
becomes the branch that skips `finalize_command_workspace`. Verify with a test that
cancels a command mid-write and asserts the shared LayerStack manifest is unchanged.

### Carve-out (explicitly NOT migrated)

Per-op overlays (`ops/files.rs`, `plugins/overlay.rs`) are **not** workspace runs —
no PTY, no lifetime beyond the synchronous op. They keep `EphemeralWorkspaceOps` /
`finalize_publishable_workspace` as-is and never enter the registry. Interrupting
them, if ever needed, is `op_cancel` at the invocation level.

## 4. Migration approach

**Option B — re-parent + re-key (recommended).** Keep the `CommandSession`
substrate and its reaping/signalling in `eos-command-session`; replace the flat
`CommandSessionRegistry` (session-id → session) with the `WorkspaceRunRegistry`
(workspace → its sessions). Daemon-wide concerns (reap, completion, count,
cleanup, the enter gate) iterate workspace runs instead of the flat map. This is a
**re-homing of ownership**, not a rewrite of the PTY lifecycle.

**Option C — full per-workspace substrate.** Move the completion queue and reaper
into each workspace run. Cleanest encapsulation, but relocates the central reaper /
completion plumbing the agent-core heartbeat drains. Higher risk; not recommended
unless B proves insufficient.

The rest of this spec assumes **Option B**.

## 5. Changes by area

### 5.1 Create

| Item | Home | Purpose |
|---|---|---|
| `WorkspaceRunId` newtype | `eos-daemon` | identify a run on the wire (ephemeral: = `command_session_id`; isolated: = `caller_id`) |
| `trait WorkspaceRun { command_sessions; cancel_workspace_run }` | `eos-daemon` | uniform teardown across the two kinds |
| `EphemeralWorkspaceRun` (owns 1 `CommandSession` + overlay + lease + scratch) | `eos-daemon` (+ `eos-ephemeral-workspace` for overlay parts) | promote the command-bound ephemeral workspace to a first-class run |
| `IsolatedWorkspaceRun` (owns N `CommandSession`s + namespace/veth/cgroup/lease/scratch) | `eos-daemon` (+ `eos-isolated-workspace`) | wrap the existing `IsolatedSession` per caller |
| `WorkspaceRunRegistry { ephemeral, isolated }` | `eos-daemon` (replaces the `OnceLock<CommandSessionManager>` state role) | the two daemon-held lists |
| `cancel_workspace_run` impls | `eos-daemon` | ephemeral: cancel its 1 session + overlay/scratch; isolated: reuse `session.exit` (already bundles its sessions + teardown) |
| `cancel_all_workspace_runs(sandbox)` | `eos-daemon` | iterate both lists → `cancel_workspace_run`; then `reap_orphan_resources`; (cancellation spec adds the GATE + `commit_to_workspace`) |

### 5.2 Re-home / change

| Current | Becomes |
|---|---|
| `CommandSessionRegistry.sessions` (flat map) | sessions owned by their `WorkspaceRun`; registry indexes runs, not sessions |
| `count_by_caller(caller_id)` | count over the caller's runs' sessions (drives the isolated enter gate) |
| `cleanup_caller(caller_id)` | `cancel_workspace_run` for that caller's run(s) |
| `collect_completed` / `push_completed` / `sweep_expired` | iterate `WorkspaceRunRegistry` → each run's sessions (completion queue stays daemon-level under Option B) |
| isolated `active_command_sessions` count + `cleanup_command_sessions_for_caller` | the `IsolatedWorkspaceRun.sessions` it owns directly (no call back into a global manager) |
| `exec_command` handler | resolve-or-create the caller's `WorkspaceRun` (ephemeral new run; isolated append session) and register it |

### 5.3 Drop

| Item | Why |
|---|---|
| `static MANAGER: OnceLock<CommandSessionManager>` as the *registry* | replaced by `WorkspaceRunRegistry` (the `CommandSession` substrate + signal/reap helpers stay; only the flat ownership map goes) |
| flat session-id keying + `caller_id`-mode partition logic | ownership is now explicit per workspace run |

### 5.4 Wire-op impact (shape preserved)

All existing ops keep their wire contract; they resolve a workspace run internally:

| Op | Resolution under the registry |
|---|---|
| `op_exec_command` | resolve-or-create the caller's run; isolated → append session, ephemeral → new run = new session |
| `op_command_write_stdin` / `op_command_read_progress` / `op_command_cancel` | look up the run that owns `command_session_id`, act on that session |
| `op_command_collect_completed` | drain completions across runs (or by caller) |
| `op_command_session_count` | count across the caller's run(s) — feeds the isolated enter gate |
| `op_enter` (isolated) | reject if the caller has any live ephemeral run / active sessions (now read directly from the registry) |
| `op_exit` (isolated) | `cancel_workspace_run` for the `IsolatedWorkspaceRun` |

## 6. Invariants to preserve

- **Cardinality:** ephemeral run owns exactly one command session; isolated run owns
  many. Enforced at `exec_command` resolution.
- **A command session belongs to exactly one workspace run** — removes the
  ephemeral-vs-isolated `caller_id` partition entirely.
- **Central reaping/signalling unchanged** (Option B): `CommandSession` keeps owning
  pgid/process and SIGTERM→SIGKILL; only its parent changes.
- **Completion delivery unchanged**: the agent-core heartbeat must still drain
  completions (now sourced by iterating runs).
- **Enter gate semantics unchanged**: entering isolated mode still rejects active
  background work — now answered by the registry rather than a side count.
- **Per-op overlays untouched** (carve-out §3).

## 7. Migration phases & verification

1. **Introduce the model (no behavior change).** Add `WorkspaceRunId`,
   `WorkspaceRun`, `EphemeralWorkspaceRun`, `IsolatedWorkspaceRun`,
   `WorkspaceRunRegistry`; keep the flat manager in place behind it. Verify:
   `cargo check -p eos-daemon -p eos-command-session --all-targets`.
2. **Re-home ephemeral.** `exec_command` (non-isolated) creates an
   `EphemeralWorkspaceRun` owning its one session; route `write_stdin` /
   `read_progress` / `cancel` / `count` through it. Verify: command-session matrix
   E2E (`eos-e2e-test/tests/eos-command-session/*`).
3. **Re-home isolated.** `IsolatedWorkspaceRun` owns its sessions directly; drop the
   `cleanup_command_sessions_for_caller` → global-manager call; `op_exit` =
   `cancel_workspace_run`. Verify: isolated lifecycle E2E + enter-gate test.
4. **Re-point daemon concerns.** `collect_completed` / `sweep_expired` / heartbeat
   iterate the registry. Verify: completion-delivery + backpressure E2E.
5. **Add `cancel_workspace_run` + `cancel_all_workspace_runs`.** Verify: a
   cancel-all E2E asserts no live sessions / leases afterward.
6. **Remove the flat registry.** Delete the `OnceLock<CommandSessionManager>`
   ownership map; keep substrate helpers. Verify:
   `cargo clippy -p eos-daemon -p eos-command-session --all-targets -- -D warnings`,
   then the full `eos-command-session` + isolated E2E suites.

### Success criteria

- The daemon holds two workspace-run lists; every command session is owned by
  exactly one run (ephemeral = 1, isolated = N).
- All existing command-session and isolated wire ops behave identically (same E2E
  results), now routed through the registry.
- `cancel_all_workspace_runs` tears down every run's sessions + workspace resources
  with no `caller_id` partition logic.
- Per-op overlays are unchanged and never registered.

## 8. Risks & open questions

- **Completion queue placement (Option B vs C).** B keeps the completion queue
  daemon-level (sourced by iterating runs); confirm the heartbeat's
  `collect_completed` semantics survive the re-keying without changing delivery
  order or the reported-once guarantee.
- **`caller_id` → run resolution.** Wire ops arrive with `caller_id` /
  `command_session_id`; need an index to resolve the owning run in O(1). Ephemeral
  run id = command_session_id makes session-targeted ops direct; the enter gate
  needs caller_id → runs.
- **Isolated multi-session teardown ordering.** `IsolatedWorkspaceRun.cancel` must
  cancel all its sessions before namespace/lease teardown — `session.exit` already
  does this; confirm parity when sessions are owned directly.
- **Sweep/TTL reaper** (`sweep_expired`) currently per-session; under runs it must
  still expire individual ephemeral runs (each = one session) and individual
  isolated sessions without tearing down the persistent isolated run.
