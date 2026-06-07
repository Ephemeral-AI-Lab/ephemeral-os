# Uniform Recursive Cancellation — ARCHIVED SPLIT SPEC

Status: superseded / split
Owner: agent-core (cancellation)
Scope: `eos-runtime`, `eos-engine`, `eos-workflow`, `eos-state`, `eos-db`,
`eos-tools` (ports), `eos-sandbox-port`, `backend-server` (launcher/api),
`sandbox/*` (daemon checkpoint/isolated/command-session ops)

This document is retained as the original monolithic cancellation proposal.
Current ownership is split into three narrower specs:

- Agent-core runtime/background/cancellation:
  `docs/plans/agent_run_local_background_supervisor_SPEC.md`
- Sandbox workspace-run cancellation substrate:
  `docs/plans/daemon_workspace_run_registry_SPEC.md`
- Backend-server API/runtime wiring:
  `docs/plans/backend_server_cancellation_wiring_SPEC.md`

## 1. Goal & motivation

Replace today's multi-mechanism cancellation — a backend future-drop, a `Drop`
guarded `BackgroundRunFinalizer`, a request-wide flat sweep, an `AbortHandle`
side-map, and a precedence latch — with **two mutually-recursive primitives**:

- `cancel_task(task_id, reason)`
- `cancel_agent_run(agent_run_id, reason)`

Everything else (request, workflow, iteration, attempt, and every spawned effect)
is expressed as decomposition into those two. Each **agent run owns its own
foreground executor and background supervisor**, and **every effect a tool spawns
is a `CancelableResource` with a teardown**, so nested `delegate_workflow`
cancellation and long-running sandbox commands both fall out of the recursion with
no special handling. A user-request cancel runs the agent-core recursion **and**
then runs an independent, sandbox-owned cleanup gate (teardown ephemeral, then
isolated, then `commit_to_workspace`) before the container is destroyed.

### Naming convention

Verb-first `cancel_<scope>`, matching the existing cancel functions in the tree
(`cancel_command_session`, `cancel_subagent`, `cancel_workflow_record`, …) and
clustering the whole cancellation surface together. Two top-level entry points
name their domain:

- `cancel_agent_core_user_request(request_id, reason)` — agent-core side
- `cancel_sandbox_user_request(sandbox_id, reason)` — sandbox side

Primitives: `cancel_task`, `cancel_agent_run`, `cancel_workflow`,
`cancel_iteration`, `cancel_attempt`. Pre-existing codebase symbols
(`cancel_command_session`, `cancel_for_parent_exit`, `cancel_workflow_state`,
`commit_to_workspace`, etc.) keep their current names.

### Defects in the current design (what this fixes)

1. **`agent_run` + message records leak on cancel.** `finish_agent_run_if_requested`
   / `message_record.finish` live in the non-`Drop`-guarded tail of
   `agent_loop.rs:148-166`; an aborted run is dropped at `stream.next().await`
   (agent_loop.rs:121) and never reaches them. On a hard cancel *every run in the
   subtree* is aborted, so every record at every depth is left open.
2. **Fire-and-forget recursion.** `BackgroundRunFinalizer::Drop` (parent_exit.rs:50)
   `tokio::spawn`s cleanup and is never awaited; the request reports `Cancelled`
   while deep recursion is still propagating. Runtime teardown can truncate it.
3. **Agent-core request state never finalized on hard cancel.** The backend drops
   the inline `run_request` future (launcher.rs:148-151), so the entry tail
   (entry.rs:267-278) never runs: the root `Task` stays `Running` and the
   agent-core request is never finalized; only backend `run_meta` flips to
   `Cancelled`.
4. **Long-running sandbox tools are uncancelable mid-flight.** A command started by
   `exec_command` lives in the daemon/PTY; it is only registered for cleanup
   *after* `yield_time_ms` if still running, leaving the wait window uncancelable.
5. **Cancel destroys the container without persisting work.** The only cancel-time
   sandbox action today is `SandboxManager::release` → Docker `stop`/`remove`
   (sandbox_manager.rs:381, docker.rs:272-285). Nothing calls `commit_to_workspace`,
   so uncommitted shared LayerStack data is lost.

### Design invariants

- **Awaited end-to-end.** No `Drop`-based or `tokio::spawn` fire-and-forget in any
  cancel path.
- **Uniform recursion.** Workflow/iteration/attempt cancellation reduces to
  `cancel_task` / `cancel_agent_run`. Nested delegation needs no extra code.
- **Per-run ownership.** Each agent run owns its `StopSignal`, its
  `ForegroundExecutor`, and its `BackgroundSupervisor`; there is no request-scoped
  supervisor.
- **Every effect is a `CancelableResource`.** The tool that spawns an effect
  supplies its teardown. Leaf tools spawn nothing and register nothing.
- **Idempotent via CAS + registry presence.** Re-entry is safe: the task status
  CAS no-ops once `Cancelled`, and a removed run is absent from the registry.
  Replaces the `precedence` latch and the `armed` flag. Teardowns must be
  idempotent.
- **Latch before terminate.** An attempt latches *all* its tasks to `Cancelled`
  before tearing down any live run, so the scheduler cannot launch a pending task
  into the gap.
- **Teardown before commit.** Sandbox teardown releases snapshot leases;
  `commit_to_workspace` is blocked while any lease is active. So isolated/ephemeral
  teardown must precede commit (a hard constraint, see §3).
- **Signal vs operation.** The `StopSignal` is the cooperative half — the loop
  polls it at **turn boundaries** (the provider stream is not cancel-safe — never
  interrupt mid-stream). The `*_cancel` functions are the imperative half — they
  raise the signal and actively tear down already-spawned effects.

## 2. Top-down flow

```
DELETE /api/user-requests/{id}                          [backend-api] user_requests.rs:130
  ├─ cancel_agent_core_user_request(request_id, reason)  ══ AWAITED ══  [eos-runtime] NEW
  │    ├─ root = root_task_id_for(request_id)             entry.rs:59     reuse as-is
  │    ├─ cancel_task(root, reason)                       [CancelPort]    NEW   (full agent-core recursion → releases sandbox leases)
  │    └─ request_store.finish_request(Cancelled)         reuse (+variant)
  ├─ cancel_sandbox_user_request(sandbox_id, reason)     ══ AWAITED ══  [host → NEW daemon gate] (see §3)
  │    ├─ Stage 1: cancel + clean up ALL ephemeral workspaces (incl. their PTY sessions)
  │    ├─ Stage 2: cancel + clean up ALL isolated workspaces (incl. their PTY sessions) + reap_orphan_resources()
  │    └─ Stage 3: ── GATE ── assert no active leases → commit_to_workspace(workspace_root)
  └─ backend reaper: SandboxManager::release → destroy container         reuse — runs AFTER both, AFTER commit

cancel_task(task_id, reason)                             [eos-engine ∷ CancelPort]  NEW
  ├─ set_task_status_if_current({Pending,Running}→Cancelled)   reuse CAS (+variant)
  └─ if run = registry.agent_run_for_task(task_id): cancel_agent_run(run, reason)

cancel_agent_run(run_id, reason)                         [eos-engine ∷ CancelPort]  NEW
  ├─ 1. ctrl.stop.request(reason)              StopSignal → query-loop turn-boundary check
  ├─ 2. ctrl.foreground.teardown(reason)       abort in-flight leaf futures; advisor nested run → cancel_agent_run(child)
  ├─ 3. ctrl.background.teardown(reason)        command sessions (daemon) + subagents (cancel_agent_run) + workflows (cancel_workflow)
  ├─ 4. finish_agent_run(Cancelled) + message_record.finish(Cancelled)   reuse fns — FIX the leak
  └─ 5. registry.remove(run_id)
        // steps 2 & 3 both iterate Vec<dyn CancelableResource> — the only difference
        // is the collection (in-flight foreground vs detached background).

cancel_workflow(wf)  → for it in open iterations: cancel_iteration(it); workflow_store.set(Cancelled)   [eos-workflow] NEW
cancel_iteration(it) → for at in open attempts:   cancel_attempt(at);   iteration_store.set(Cancelled)  [eos-workflow] NEW
cancel_attempt(at)                                                                                       [eos-workflow] NEW
  ├─ tasks = planner_task_id ∪ generator_task_ids ∪ reducer_task_ids
  ├─ latch_attempt_tasks_cancelled(tasks {Pending,Running}→Cancelled)    NEW bulk store method  ← LATCH FIRST
  ├─ for t in tasks: cancel_task(t, reason)     [CancelPort] ⟲   planner stops driver+RUN JoinSet; gen/reducer → nested
  └─ attempt_store.close(Cancelled)             reuse close (+AttemptClosure::Cancelled)
```

Recursion, awaited end-to-end:
`cancel_task(generator|reducer) → cancel_agent_run → background.teardown → cancel_workflow(nested) ⟲`.

### 2.1 Ownership tree

```
Request ──root_task_id──► Task(root)
                            └─ AgentRun  (executes the task) ──► AgentRunControl
                                 ├─ StopSignal            → its query loop (turn-boundary stop)
                                 ├─ ForegroundExecutor    → in-flight fg tool futures + inline nested run (advisor)
                                 └─ BackgroundSupervisor   → detached effects (yielded commands, subagents, workflows)
                            every spawned effect = a CancelableResource
                            (teardown supplied by the tool that created it)
                              ├─ delegated workflow   → cancel_workflow ⟲
                              ├─ subagent run         → cancel_agent_run ⟲
                              ├─ command session      → cancel_command_session (daemon)
                              └─ advisor nested run   → cancel_agent_run ⟲
```

### 2.2 Tool taxonomy (foreground vs background; who manages; teardown)

Foreground management is deliberately **thinner** than the background supervisor.
Background work is *detached*, so the supervisor must track it, poll it via the
heartbeat, and deliver notifications. Foreground work is *awaited inline by the
loop*, so the loop already manages it; the only thing added is cancel-reachability.
`ForegroundExecutor` is therefore the dispatch `JoinSet` promoted to a named,
cancel-reachable handle plus links to any inline nested runs — **not** a parallel
supervisor (no records, no heartbeat, no delivery).

| Tool | Nature | Managed by | Teardown |
|---|---|---|---|
| `read` / `write` / `edit` / `search` | foreground, short | `ForegroundExecutor` (JoinSet) | abort the future (no external effect) |
| `exec_command` / `write_stdin` | foreground wait `yield_time_ms`, *may* background | `ForegroundExecutor` holds the wait-future; **command session registered as a resource at creation** | `cancel_command_session` (daemon RPC) |
| `ask_advisor` (ask-helper) | foreground, **nested agent run** | `ForegroundExecutor` (inline nested-run link) | `cancel_agent_run(child)` |
| `run_subagent` | **always background** | `BackgroundSupervisor` | `cancel_agent_run(child)` |
| `delegate_workflow` | background | `BackgroundSupervisor` | `cancel_workflow` |

The `exec_command` foreground→background transition is **not** a special cancel
case: the command-session `CancelableResource` (teardown = daemon kill) is
registered once at creation and stays cancelable throughout; the
`ForegroundExecutor`/`BackgroundSupervisor` split only governs *who holds the
wait-future and who polls for completion*, not the teardown.

## 3. Sandbox finalization on request cancel

A user-request cancel must also finalize the **sandbox**: tear down the workspaces
(and the PTY/command sessions they own), then **persist** the shared workspace
LayerStack with `commit_to_workspace`, then return — before the backend destroys
the container.

### Two layers — agent-core *calls*, the sandbox *gates*

Do **not** assume the agent-core recursion left the sandbox clean. Two independent
layers, defense-in-depth:

- **agent-core calls the cancel requests.** During its recursion, `cancel_agent_run`
  issues `cancel_command_session` / `exit_isolated_workspace` for its own resources.
  This is for **promptness and scoping** — cancelling one subagent kills *its* PTY
  immediately, not at request-end.
- **the sandbox owns an authoritative cleanup gate.** `cancel_sandbox_user_request`
  does **not** trust agent-core finished. It re-enumerates everything by
  `sandbox_id` and tears it down, then commits. Because it must authoritatively
  enumerate every live workspace / PTY for a sandbox and refuse to commit until
  they are gone, the gate is **daemon-owned** — a new daemon op `api.cancel_sandbox`
  runs the three stages inside the daemon; `cancel_sandbox_user_request` is the host
  wrapper that calls it.

### `cancel_sandbox_user_request(sandbox_id, reason)` — three stages

PTY/command sessions are **owned by a workspace** (ephemeral in the normal path,
isolated in isolated mode), so they are torn down *with* their workspace, not as a
separate flat step:

```
Stage 1 — ephemeral workspaces:
   for each live ephemeral workspace in the sandbox:
       cancel its PTY/command sessions (SIGTERM→SIGKILL)         cancel_command_session / CommandSessionManager::cancel
       unmount overlay, remove upperdir + scratch                RunDirCleanup / cleanup_workspace
Stage 2 — isolated workspaces:
   for caller in list_open_isolated():                           op_list_open → session.list_open_callers
       session.exit(caller, grace)                               op_exit: cleanup_command_sessions_for_caller (its PTY)
                                                                  + kill ns-holder + release lease + teardown veth
                                                                  + cgroup rmdir + discard upperdir + rmtree scratch
   reap_orphan_resources()                                       GC handle-less eos-iws-* veth/cgroup/scratch leftovers
Stage 3 — GATE + commit:
   assert no active leases                                       enforce Stages 1&2 fully released
   commit_to_workspace(workspace_root)                           persist shared LayerStack → workspace repo
   return
(then backend reaper: SandboxManager::release → destroy container)
```

### `reap_orphan_resources` is NOT the teardown engine

`IsolatedSession::reap_orphan_resources` → `reap_named_orphans`
(`eos-isolated-workspace/src/session/gc.rs:124`) is **isolated-only, orphan-only
GC**: it sweeps `eos-iws-*` host resources that have **no live handle** — named
`veth` (`teardown_host_veth`), named `cgroup` (`kill_cgroup_pids` + `remove_dir`),
and named `scratch` (`remove_dir_all`). It runs at daemon startup / test reset.

It does **not**: cancel PTY/command sessions (that is `cancel_command_session`,
`eos-command-session`), touch ephemeral workspace mounts (`eos-ephemeral-workspace`),
or tear down a *live* isolated workspace (that is `session.exit`). It is the
last-resort leftover GC inside Stage 2, not the cleanup itself.

### Teardown function per resource (authoritative)

| Resource | Teardown | What it does |
|---|---|---|
| PTY / command session | `cancel_command_session` → `CommandSessionManager::cancel` | SIGTERM→SIGKILL on the pgid |
| **live** isolated workspace | `session.exit(caller, grace)` (`op_exit`) | clean its PTY, kill ns-holder, release lease, teardown veth, cgroup rmdir, **discard upperdir**, rmtree scratch |
| **orphaned** isolated resources | `reap_orphan_resources` | GC handle-less `eos-iws-*` veth/cgroup/scratch |
| ephemeral workspace mount + scratch | `RunDirCleanup` Drop / `cleanup_workspace` | removed when its command session ends |
| shared LayerStack | `commit_to_workspace` | persist → workspace repo (lease-gated) |

### Lease ordering is a hard constraint

`LayerStack::commit_to_workspace` is **"blocked by active leases"**
(`eos-layerstack/src/stack.rs:434`). Isolated and ephemeral workspaces hold
snapshot leases, so Stages 1 & 2 must complete before Stage 3 — the Stage-3 GATE
makes that explicit (assert no active leases, then commit). Isolated-workspace
writes are intentionally **discarded** on exit (captured and audited, never
OCC-published), so `commit_to_workspace` persists the **shared** workspace
LayerStack accumulated via OCC, not isolated scratch; isolated teardown is purely
to release leases.

### Existing sandbox functions (reusable building blocks)

| Capability | Daemon op | Host wrapper (`eos-sandbox-port`) | Core impl | Status |
|---|---|---|---|---|
| Commit LayerStack → workspace repo | `api.commit_to_workspace` `ops/checkpoint.rs:38` | **none — CREATE** | `LayerStack::commit_to_workspace` `stack.rs:419` | wired in daemon, **zero host callers today** |
| Exit one isolated workspace | `op_exit` `isolated_workspace/mod.rs:90` | `exit_isolated_workspace` `tool_api/isolated.rs:46` | `session.exit` | reuse as-is |
| Enumerate open isolated workspaces | `op_list_open` `mod.rs:139` → `session.list_open_callers()` | **none — CREATE** | — | reuse |
| Teardown-all isolated (template) | `op_test_reset` `mod.rs:153-178` | — | `reap_orphan_resources` | test-gated; proves primitives |
| Cancel one command session | `op_command_cancel` | `cancel_command_session` `tool_api/command.rs:94` | `CommandSessionManager::cancel` | reuse as-is |
| Destroy the container | — (backend) | — | `SandboxManager::release`/`destroy` `sandbox_manager.rs:381,135` → Docker `docker.rs:272-285` | reuse — must run **after** commit |

## 4. Functions / code to DROP

Aggressive removal — these are obsolete or would require heavy patching to fit
the new model. Prefer rewrite over patch.

| Item | Location | Replaced by |
|---|---|---|
| `BackgroundRunFinalizer` — struct, `new`, `finalize`, `disarm`, **`Drop` impl** | `eos-engine/src/background/parent_exit.rs` (whole file) | explicit awaited `cancel_agent_run` |
| `BackgroundSupervisorHandle::cancel_for_parent_exit` + `cancel_command_session_for_parent_exit` + the `BackgroundSupervisorPort::cancel_for_parent_exit` impl | `handle.rs:58`, `handle.rs:103`, `subagent.rs:426` | `BackgroundSupervisor::teardown` iterating `dyn CancelableResource` |
| Bespoke per-category cancel methods `cancel_subagent` / `cancel_workflow_record` / `cancel_command_record` | `supervisor.rs:213,224,298` | each record type's `CancelableResource::teardown` impl |
| Request-scoped `BackgroundSupervisorHandle` creation | `eos-runtime/src/entry.rs:109` | per-run executor + supervisor owned by `AgentRunControl` |
| entry cleanup tail: `cancel_for_parent_exit(None, …)` + heartbeat-abort-as-cancel | `entry.rs:268-273` | `cancel_agent_core_user_request` recursion |
| `WorkflowControlAdapter::cancel_workflow_state` | `eos-workflow/src/ports.rs:261` | `cancel_workflow` / `cancel_iteration` / `cancel_attempt` decomposition |
| `WorkflowControlAdapter::cancel_active_task` | `ports.rs:336` | generic `cancel_task` (via `CancelPort`) |
| `AttemptOrchestratorRegistry::abort_planner` + `store_planner_abort_with` + `planner_aborts` field | `attempt/orchestrator_registry.rs:19,64,74` | `cancel_task(planner_task_id)` raising the per-run `StopSignal` |
| `BackgroundTaskStatus::precedence` + the precedence check in `settle_subagent` | `supervisor.rs:35`, `supervisor.rs:205` | status CAS + registry presence (idempotency) |
| `matches_agent_run` None-sweep + every `*_for_agent_run(Option<&AgentRunId>)` variant (`cancel_subagents_for_agent_run`, `running_workflows_for_agent_run`, `running_commands_for_agent_run`, `inflight_report(Option)`) | `supervisor.rs:112,239,262,278,317` | per-run no-arg lists on the per-run supervisor |
| `BackgroundSupervisorHandle::inner` (direct global-supervisor escape hatch) | `handle.rs:49` | `AgentRunRegistry::get(agent_run_id)` |
| *(optional)* subagent-driver `AbortHandle` side-map (`store_handle` / `take_and_abort_handle` / `forget_handle` / `handles` field) | `supervisor.rs:129,344-359` | `cancel_agent_run(sub)` via the sub's `StopSignal` — see §8 granularity note |

Notes:
- Two audits split on `cancel_workflow_state` (rewrite vs keep). **Decision: drop
  and decompose** — it inlines per-task cancel with no latch phase and does not
  match the `workflow → iteration → attempt` hierarchy; it is a rewrite, not a
  patch.
- The typed records (`SubagentRecord`, `WorkflowBackgroundRecord`,
  command-session records) **stay** for their non-cancel lifecycle (progress,
  completion ingestion, delivery); they additionally `impl CancelableResource`.
- Dropping the `Option` None-sweep variants requires updating every call site
  (supervisor.rs:271,287,324,332; handle.rs:70,71; command_session.rs:211) to the
  per-run no-arg forms.
- **No sandbox code is dropped** — the existing teardown/commit ops are reused; the
  gap is host wrappers + the `cancel_sandbox_user_request` orchestration (see §3/§5).

## 5. Functions / types to CREATE

```rust
/// The cooperative stop flag a run's query loop polls at turn boundaries.
/// Newtype over tokio_util::sync::CancellationToken; the reason rides in
/// AgentRunControl (the token carries no payload).
pub struct StopSignal(/* CancellationToken */);
impl StopSignal {
    pub fn request(&self);          // raise — was token.cancel()
    pub fn is_requested(&self) -> bool;
    pub async fn requested(&self);  // await the stop
    pub fn child(&self) -> StopSignal;  // for inline nested runs (advisor)
}

/// A live, cancelable effect a tool created during an agent run.
/// The tool that creates the effect supplies the teardown. Leaf tools create none.
#[async_trait]
pub trait CancelableResource: Send + Sync {
    async fn teardown(&self, reason: &str) -> Result<(), ToolError>;
}

/// Two recursive cancellation primitives. Home: eos-tools (shared port crate),
/// implemented in eos-engine, so eos-workflow ↔ eos-engine recurse without a cycle.
#[async_trait]
pub trait CancelPort: Send + Sync {
    async fn cancel_task(&self, task_id: &TaskId, reason: &str) -> Result<(), ToolError>;
    async fn cancel_agent_run(&self, run_id: &AgentRunId, reason: &str) -> Result<(), ToolError>;
}

struct AgentRunControl {
    stop: StopSignal,                  // §2 — cooperative stop flag the loop polls
    foreground: ForegroundExecutor,    // in-flight fg tool futures + inline nested runs (advisor)
    background: BackgroundSupervisor,  // detached effects (yielded commands, subagents, workflows)
    task_id: TaskId,
    // + agent_run / message_record handles for finalize
}
```

| Item | Home | Purpose |
|---|---|---|
| `StopSignal` newtype | `eos-engine` (or `eos-tools`) | cooperative stop flag; renames/encapsulates the raw `CancellationToken` |
| `trait CancelableResource { teardown }` | `eos-tools/ports` | uniform teardown for every tool-spawned effect |
| `trait CancelPort { cancel_task; cancel_agent_run }` | `eos-tools/ports` | shared seam so `eos-workflow` ↔ `eos-engine` recurse without a crate cycle |
| `cancel_agent_core_user_request(request_id, reason)` | `eos-runtime` | request → `root_task_id_for` → `cancel_task(root)` → `finish_request(Cancelled)` |
| `cancel_sandbox_user_request(sandbox_id, reason)` | `eos-runtime` (orchestration over `eos-sandbox-port`) | sweep isolated (`list_open`→`exit`) + `reap_orphan_resources` → `commit_to_workspace` (§3) |
| `cancel_task` / `cancel_agent_run` (impl `CancelPort`) | `eos-engine` | the two recursive primitives |
| `ForegroundExecutor` | `eos-engine` | per-run, lightweight: owns the dispatch `JoinSet` (abort in-flight fg futures) + links to inline nested runs (advisor); `teardown` iterates its `CancelableResource`s |
| `BackgroundSupervisor::teardown(reason)` | `eos-engine/background` | per-run fan-out: iterate detached `CancelableResource`s (command sessions → daemon, subagents → `cancel_agent_run`, workflows → `cancel_workflow`) |
| `AgentRunRegistry` + `AgentRunControl { stop, foreground, background, task_id, … }` + `task_id → agent_run_id` index | `eos-engine` | make live runs/tasks addressable; `agent_run_for_task`, `get`, `remove` |
| `cancel_workflow` / `cancel_iteration` / `cancel_attempt` | `eos-workflow` | 3-level decomposition; `cancel_attempt` latches then `cancel_task` per task |
| `TaskStore::latch_attempt_tasks_cancelled(attempt_id, ids)` (bulk CAS) | `eos-db` (+ trait in `eos-state`) | atomic latch so the scheduler can't launch into the gap |
| Host wrapper `commit_to_workspace(workspace_root)` | `eos-sandbox-port` | call the existing `api.commit_to_workspace` daemon op (no host caller today) |
| Host wrapper `list_open_isolated()` | `eos-sandbox-port` | call the existing `op_list_open` daemon op to enumerate isolated workspaces for the sweep |
| `CancelableResource` impls per effect: workflow handle, subagent run, command session, advisor nested run | `eos-engine` / `eos-workflow` | the teardown functions (see §2.2) |

## 6. Code to REUSE (load-bearing — keep as-is)

- **`set_task_status_if_current`** + its SQL (`request_task.rs:18`) — the per-task
  latch CAS primitive.
- **`AttemptStore::close` / `IterationStore::set_status` / `WorkflowStore::set_status`**
  — terminal writers, already generic over the status enums.
- **`IterationStatus::Cancelled`, `WorkflowStatus::Cancelled`,
  `IterationOutcome::Cancelled`, `WorkflowOutcome::Cancelled`** — already exist.
- **`WorkflowStarter::compensate_failed_start`** (`starter.rs:125`) — already runs
  the attempt→iteration→workflow `Cancelled` sequence; a template for
  `cancel_attempt`.
- **`close_attempt` / `close_workflow` / `cancellation_outcomes` /
  `WorkflowHandleRegistry`** — orchestration helpers, unchanged.
- **`SubagentRecord` / `WorkflowBackgroundRecord` / command-session records** —
  kept for non-cancel lifecycle; each gains a `CancelableResource::teardown` impl.
- **`reaper.rs` / `RunHost` / `Disposition::Cancelled`** — backend finalize path.
- **`AgentRunStore::get_for_task`** — persisted task→run fallback when no live run.
- **`run_agent` / `run_advisor`** — reused, modified only to thread the `StopSignal`.
- **`dispatch_many_foreground_tools` + `JoinSet::abort_all`** — the JoinSet becomes
  the `ForegroundExecutor`'s abort handle.
- **Sandbox (§3)**: `LayerStack::commit_to_workspace` + `api.commit_to_workspace`,
  `exit_isolated_workspace` + `op_exit`, `op_list_open` / `list_open_callers`,
  `reap_orphan_resources`, `cancel_command_session` + `op_command_cancel`,
  `SandboxManager::release`/`destroy`.
- **`tokio_util::sync::CancellationToken`** — the substrate `StopSignal` wraps.

## 7. State / store changes (gating — break exhaustive matches)

| Add variant | Then update |
|---|---|
| `TaskStatus::Cancelled` (`eos-state/src/task.rs:17`) | `is_terminal_generator()`; reachability `matches!` at `plan_dag.rs:50` (decide: `Cancelled` blocks the DAG — **yes**) |
| `AttemptStatus::Cancelled` + `AttemptClosure::Cancelled { reason, outcomes, closed_at }` (+ `status()`) (`eos-state/src/attempt.rs`) | exhaustive match in `attempt_state_from_columns` (`eos-db/src/rows.rs:437`); `SqlAttemptStore::close` fail-reason extraction (`attempt.rs:125`) |
| `RequestStatus::Cancelled` (`eos-state/src/request.rs:11`) | `is_terminal()`; `reconcile()` in the detail handler (`user_requests.rs:98`) |
| *(none)* `IterationStatus::Cancelled` / `WorkflowStatus::Cancelled` | already exist — reuse |

`terminal_tool_result` on a cancelled `Task`: stamp `{ "fail_reason": "cancelled",
"reason": <reason> }` for parity with the existing `cancel_workflowled` marker;
iteration/workflow `outcomes` columns stay the empty typed projection `[]`.

**Command-session teardown registered at creation.** Today a command session is
only registered for cleanup *after* `yield_time_ms` if still running, leaving the
wait window uncancelable. New rule: `exec_command` registers the command session
as a `CancelableResource` (teardown = `cancel_command_session`) **at creation**.

## 8. Decisions & sharp edges

1. **Backend stops dropping the future.** `launcher.cancel` / `run_to_completion`
   **await** `cancel_agent_core_user_request` then `cancel_sandbox_user_request`;
   the agent-core `StopSignal` makes `run_request` self-terminate and return
   `Disposition::Cancelled`. The `RunSlot` / biased-`select!` may then be
   simplified or removed.
2. **Container destroyed only after commit.** The reaper's `SandboxManager::release`
   must run **after** `cancel_sandbox_user_request` returns, so
   `commit_to_workspace` persists the shared LayerStack before the container dies
   (fixes defect #5).
3. **Stop-signal granularity tradeoff.** A `StopSignal` takes effect at query-loop
   turn boundaries (provider stream not cancel-safe). Dropping the
   planner/subagent `AbortHandle` side-maps trades immediate abort for clean
   turn-boundary stop. The `ForegroundExecutor` keeps `abort_all` as the backstop
   for in-flight leaf tool futures.
4. **Signal vs teardown are both required.** `StopSignal` stops the loop from
   issuing *more* work; `CancelableResource::teardown` cleans up what was already
   spawned. The advisor needs both: its child `StopSignal` delivers the signal,
   and its teardown (`cancel_agent_run(child)`) awaits its finalization.
5. **Foreground executor is lightweight, not a mirror supervisor.** Foreground
   work is awaited inline, so the executor only needs abort + inline-nested-run
   links — no records, heartbeat, or delivery.
6. **Heartbeat must go registry-aware.** `spawn_command_completion_heartbeat`
   (`heartbeat.rs:37`) is request-scoped over the single supervisor today; with
   per-run supervisors it must iterate `AgentRunRegistry` (or be per-run).
7. **Idempotency = CAS + registry presence** (replaces `precedence` and `armed`).
   Teardowns must be idempotent (the resource may already be gone).
8. **Latch covers all three task kinds.** `cancel_attempt` must latch
   `planner_task_id ∪ generator_task_ids ∪ reducer_task_ids` before any
   `cancel_task`.
9. **Lease ordering (sandbox).** `commit_to_workspace` is lease-gated; isolated and
   ephemeral teardown (which the agent-core recursion + the §3 sweep perform) must
   complete before commit.
10. **No parent mutation.** `cancel_workflow` must not touch the parent task
    (existing invariant GC-eos-workflow-01/02).

## 9. Crate dependency / dispatch

- `CancelPort`, `CancelableResource`, and `WorkflowControlPort` all live in
  `eos-tools` (the shared port crate). `eos-engine` implements `CancelPort`;
  `eos-workflow` implements `WorkflowControlPort`. `eos-runtime` wires both `dyn`
  ports together at the composition root and owns the two `*_user_request_cancel`
  entry points. No cross-crate back-edge; recursion ping-pongs across the two
  ports exactly as `engine ↔ workflow` already communicate.
- `cancel_sandbox_user_request` lives in `eos-runtime` and drives `eos-sandbox-port`
  host wrappers (wire calls to the daemon); no new daemon ops are required.
- `StopSignal` wraps `tokio_util::sync::CancellationToken`; inline nested runs
  (advisor) get `stop.child()`.
- `CancelableResource` is `dyn` (tools/effects are an open set / plugins);
  object-safe, one async method.

## 10. Implementation phases & verification ladder

1. **State variants** — add `TaskStatus::Cancelled`, `AttemptStatus`/
   `AttemptClosure::Cancelled`, `RequestStatus::Cancelled`; fix exhaustive matches.
   Verify: `cargo check -p eos-state -p eos-db --all-targets`.
2. **Signal + registry** — `StopSignal`, `AgentRunRegistry`, `AgentRunControl`,
   stop field threaded into `AgentRunInput`/`QueryContext`, turn-boundary check in
   `run_query`, `QueryExitReason::Cancelled`. Verify: `cargo test -p eos-engine`.
3. **Teardown model** — `CancelableResource`; `ForegroundExecutor`;
   `BackgroundSupervisor::teardown`; per-effect teardown impls; drop
   `BackgroundRunFinalizer`, `cancel_for_parent_exit`, the bespoke per-category
   cancel methods, and the precedence latch. Verify: `cargo test -p eos-engine`
   (incl. a cancel-finalizes-records test and an advisor-cancel test).
4. **Primitives** — `CancelPort`, `cancel_task`, `cancel_agent_run`. Verify:
   `cargo test -p eos-engine`.
5. **Workflow decomposition** — `cancel_workflow/cancel_iteration/cancel_attempt`;
   drop `cancel_workflow_state` / `cancel_active_task` / `abort_planner` /
   `store_planner_abort_with`; `latch_attempt_tasks_cancelled`. Verify:
   `cargo test -p eos-workflow` (incl. a nested-delegation cancel test).
6. **Request + backend wiring** — `cancel_agent_core_user_request`; drop
   request-scoped supervisor + entry cleanup tail; launcher awaits the cancel
   entry points; reconcile handler. Verify: `cargo test -p eos-runtime`, backend
   launcher tests.
7. **Sandbox finalization** — host wrappers `commit_to_workspace` + `list_open`;
   `cancel_sandbox_user_request`; ensure reaper `release` runs after commit. Verify:
   `cargo test -p eos-sandbox-port`; a live E2E that cancels mid-run and asserts
   the workspace repo received the commit before container teardown.
8. **Heartbeat** — registry-aware. Verify: command-session completion tests, then
   `cargo clippy --workspace --all-targets -- -D warnings`.

### Success criteria

- A cancel at any nesting depth leaves **every** `Task`, `Attempt`, `Iteration`,
  `Workflow`, `agent_run`, and message record in a terminal state — no open rows.
- Every tool-spawned effect (workflow, subagent, command session, advisor run) is
  torn down via its `CancelableResource::teardown`; long-running sandbox commands
  are cancelable from creation.
- On request cancel the shared workspace LayerStack is **committed
  (`commit_to_workspace`) before the container is destroyed** — no data loss.
- Cancellation is fully awaited: when both `*_user_request_cancel` entry points
  return, no detached cleanup remains in flight.
- No `Drop`-based or `tokio::spawn` fire-and-forget in any cancel path.
- Calling cancel twice is a no-op (idempotent).
```
