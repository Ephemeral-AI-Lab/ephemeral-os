# Uniform Recursive Cancellation — SPEC

Status: proposed
Owner: agent-core (cancellation)
Scope: `eos-runtime`, `eos-engine`, `eos-workflow`, `eos-state`, `eos-db`,
`eos-tools` (ports), `backend-server` (launcher/api)

## 1. Goal & motivation

Replace today's multi-mechanism cancellation — a backend future-drop, a `Drop`
guarded `BackgroundRunFinalizer`, a request-wide flat sweep, an `AbortHandle`
side-map, and a precedence latch — with **two mutually-recursive primitives**:

- `cancel_task(task_id, reason)`
- `cancel_agent_run(agent_run_id, reason)`

Everything else (request, workflow, iteration, attempt, background work) is
expressed as decomposition into those two. Each **agent run owns its own
background supervisor**, so nested `delegate_workflow` cancellation falls out of
the recursion with no special handling.

### Defects in the current design (what this fixes)

1. **`agent_run` + message records leak on cancel.** `finish_agent_run_if_requested`
   / `message_record.finish` live in the non-`Drop`-guarded tail of
   `agent_loop.rs:148-166`; an aborted run is dropped at `stream.next().await`
   (agent_loop.rs:121) and never reaches them. On a hard cancel *every run in the
   subtree* is aborted, so every `agent_run`/message record at every depth is left
   open.
2. **Fire-and-forget recursion.** `BackgroundRunFinalizer::Drop` (parent_exit.rs:50)
   `tokio::spawn`s cleanup and is never awaited; the request reports `Cancelled`
   while deep recursion is still propagating. Runtime teardown can truncate it.
3. **Agent-core request state never finalized on hard cancel.** The backend drops
   the inline `run_request` future (launcher.rs:148-151), so the entry tail
   (entry.rs:267-278) never runs: the root `Task` stays `Running` and the
   agent-core request is never finalized; only backend `run_meta` flips to
   `Cancelled`.

### Design invariants

- **Awaited end-to-end.** No `Drop`-based or `tokio::spawn` fire-and-forget in any
  cancel path.
- **Uniform recursion.** Workflow/iteration/attempt cancellation reduces to
  `cancel_task` / `cancel_agent_run`. Nested delegation needs no extra code.
- **Per-run ownership.** Each agent run owns its `BackgroundSupervisor` and its
  `CancellationToken`; there is no request-scoped supervisor.
- **Idempotent via CAS + registry presence.** Re-entry is safe: the task status
  CAS no-ops once `Cancelled`, and a removed run is absent from the registry.
  Replaces the `precedence` latch and the `armed` flag.
- **Latch before terminate.** An attempt latches *all* its tasks to `Cancelled`
  before tearing down any live run, so the scheduler cannot launch a pending task
  into the gap.
- **Token granularity.** A `CancellationToken` takes effect at query-loop **turn
  boundaries** (the provider stream is not cancel-safe — never interrupt
  mid-stream). In-flight foreground tools are torn down immediately via
  `JoinSet::abort_all`.

## 2. Top-down flow

```
DELETE /api/user-requests/{id}                          [backend-api] user_requests.rs:130
  └─ cancel_request(request_id, reason)        ══ AWAITED ══   [eos-runtime]  NEW
       ├─ root = root_task_id_for(request_id)              entry.rs:59      reuse as-is
       ├─ cancel_task(root, reason)                        [CancelPort]     NEW
       └─ request_store.finish_request(Cancelled)          reuse (+variant)
  (launcher reaper still writes run_meta=Cancelled + releases sandbox — reuse as-is;
   launcher now AWAITS cancel_request instead of dropping the run future)

cancel_task(task_id, reason)                              [eos-engine ∷ CancelPort]  NEW
  ├─ set_task_status_if_current({Pending,Running}→Cancelled)   reuse CAS (+variant)
  └─ if run = registry.agent_run_for_task(task_id): cancel_agent_run(run, reason)

cancel_agent_run(run_id, reason)                          [eos-engine ∷ CancelPort]  NEW
  ├─ 1. ctrl.token.cancel()                               NEW token → query-loop select!
  ├─ 2. for child in ctrl.tool_children: cancel_agent_run(child)    advisor (NEW generic tool-cancel)
  ├─ 3. ctrl.supervisor.cancel(reason)                    per-run; body = old cancel_for_parent_exit fan-out
  │        ├─ a. command sessions → daemon RPC            reuse cancel_command_session body
  │        ├─ b. workflows  → cancel_workflow(wf)         [WorkflowControlPort]
  │        └─ c. subagents  → cancel_agent_run(sub)       ⟲ same primitive
  ├─ 4. finish_agent_run(Cancelled) + message_record.finish(Cancelled)   reuse fns — FIX the leak (call on cancel)
  └─ 5. registry.remove(run_id)

cancel_workflow(wf)  → for it in open iterations: cancel_iteration(it); workflow_store.set(Cancelled)   [eos-workflow] NEW
cancel_iteration(it) → for at in open attempts:   cancel_attempt(at);  iteration_store.set(Cancelled)   [eos-workflow] NEW
cancel_attempt(at)                                                                                      [eos-workflow] NEW
  ├─ tasks = planner_task_id ∪ generator_task_ids ∪ reducer_task_ids
  ├─ latch_attempt_tasks_cancelled(tasks {Pending,Running}→Cancelled)    NEW bulk store method  ← LATCH FIRST
  ├─ for t in tasks: cancel_task(t, reason)     [CancelPort] ⟲   planner stops driver+RUN JoinSet; gen/reducer → nested
  └─ attempt_store.close(Cancelled)             reuse close (+AttemptClosure::Cancelled)
```

Recursion, awaited end-to-end:
`cancel_task(generator|reducer) → cancel_agent_run → supervisor.cancel → cancel_workflow(nested) ⟲`.

### Ownership tree

```
Request ──root_task_id──► Task(root)
                            └─ AgentRun  (executes the task)
                                 ├─ CancellationToken     → its query loop
                                 ├─ tool_children          → in-flight foreground tools (advisor = child AgentRun)
                                 └─ BackgroundSupervisor   (PER RUN, not request-scoped)
                                      ├─ Workflows   → Iteration → Attempt → {planner,generators,reducers} → AgentRun ⟲
                                      ├─ Subagents   → AgentRun ⟲
                                      └─ CommandSessions → daemon RPC
```

## 3. Functions / code to DROP

Aggressive removal — these are obsolete or would require heavy patching to fit
the new model. Prefer rewrite over patch.

| Item | Location | Replaced by |
|---|---|---|
| `BackgroundRunFinalizer` — struct, `new`, `finalize`, `disarm`, **`Drop` impl** | `eos-engine/src/background/parent_exit.rs` (whole file) | explicit awaited `cancel_agent_run` |
| `BackgroundSupervisorHandle::cancel_for_parent_exit` + `cancel_command_session_for_parent_exit` + the `BackgroundSupervisorPort::cancel_for_parent_exit` impl | `handle.rs:58`, `handle.rs:103`, `subagent.rs:426` | `BackgroundSupervisor::cancel(reason)` (per-run; the salvaged fan-out body) |
| Request-scoped `BackgroundSupervisorHandle` creation | `eos-runtime/src/entry.rs:109` | per-run supervisor owned by `AgentRunControl` |
| entry cleanup tail: `cancel_for_parent_exit(None, …)` + heartbeat-abort-as-cancel | `entry.rs:268-273` | `cancel_request` recursion |
| `WorkflowControlAdapter::cancel_workflow_state` | `eos-workflow/src/ports.rs:261` | `cancel_workflow` / `cancel_iteration` / `cancel_attempt` decomposition |
| `WorkflowControlAdapter::cancel_active_task` | `ports.rs:336` | generic `cancel_task` (via `CancelPort`) |
| `AttemptOrchestratorRegistry::abort_planner` + `store_planner_abort_with` + `planner_aborts` field | `attempt/orchestrator_registry.rs:19,64,74` | `cancel_task(planner_task_id)` firing the per-run token |
| `BackgroundTaskStatus::precedence` + the precedence check in `settle_subagent` | `supervisor.rs:35`, `supervisor.rs:205` | status CAS + registry presence (idempotency) |
| `matches_agent_run` None-sweep + every `*_for_agent_run(Option<&AgentRunId>)` variant (`cancel_subagents_for_agent_run`, `running_workflows_for_agent_run`, `running_commands_for_agent_run`, `inflight_report(Option)`) | `supervisor.rs:112,239,262,278,317` | per-run no-arg lists on the per-run supervisor |
| `BackgroundSupervisorHandle::inner` (direct global-supervisor escape hatch) | `handle.rs:49` | `AgentRunRegistry::get(agent_run_id)` |
| *(optional)* subagent-driver `AbortHandle` side-map (`store_handle` / `take_and_abort_handle` / `forget_handle` / `handles` field) | `supervisor.rs:129,344-359` | `cancel_agent_run(sub)` via the sub's token — see §7 granularity note |

Notes:
- Two audits split on `cancel_workflow_state` (rewrite vs keep). **Decision: drop
  and decompose** — it inlines per-task cancel with no latch phase and does not
  match the `workflow → iteration → attempt` hierarchy; it is a rewrite, not a
  patch.
- Dropping the `Option` None-sweep variants requires updating every call site
  (supervisor.rs:271,287,324,332; handle.rs:70,71; command_session.rs:211) to the
  per-run no-arg forms.

## 4. Functions / types to CREATE

| Item | Home | Purpose |
|---|---|---|
| `trait CancelPort { async fn cancel_task(&self, task_id, reason); async fn cancel_agent_run(&self, run_id, reason); }` | `eos-tools/src/ports/mod.rs` | shared seam so `eos-workflow` ↔ `eos-engine` recurse without a crate cycle (mirrors `WorkflowControlPort`) |
| `cancel_request(request_id, reason)` | `eos-runtime` | request → `root_task_id_for` → `cancel_task(root)` → `finish_request(Cancelled)` |
| `cancel_task` / `cancel_agent_run` (impl `CancelPort`) | `eos-engine` | the two recursive primitives |
| `BackgroundSupervisor::cancel(reason)` | `eos-engine/background` | per-run fan-out: command sessions (daemon RPC) + workflows (`WorkflowControlPort`) + subagents (`cancel_agent_run`) |
| `AgentRunRegistry` + `AgentRunControl { token, supervisor, tool_children, task_id }` + `task_id → agent_run_id` index | `eos-engine` | make live runs/tasks addressable; `agent_run_for_task`, `get`, `remove` |
| `cancel_workflow` / `cancel_iteration` / `cancel_attempt` | `eos-workflow` | 3-level decomposition; `cancel_attempt` latches then `cancel_task` per task |
| `TaskStore::latch_attempt_tasks_cancelled(attempt_id, ids)` (bulk CAS: `UPDATE … WHERE id IN (…) AND status IN ('pending','running') SET status='cancelled'`) | `eos-db` (+ trait in `eos-state`) | atomic latch so the scheduler can't launch into the gap |
| advisor `tool_children` registration in dispatch | `eos-engine/tool_call/dispatch.rs` | generic foreground-tool cancellation (advisor = a tool owning a child agent run) |

## 5. Code to REUSE (load-bearing — keep as-is)

- **`set_task_status_if_current`** + its SQL (`request_task.rs:18`) — the per-task
  latch CAS primitive.
- **`AttemptStore::close` / `IterationStore::set_status` / `WorkflowStore::set_status`**
  — terminal writers, already generic over the status enums.
- **`IterationStatus::Cancelled`, `WorkflowStatus::Cancelled`,
  `IterationOutcome::Cancelled`, `WorkflowOutcome::Cancelled`** — already exist;
  zero work.
- **`WorkflowStarter::compensate_failed_start`** (`starter.rs:125`) — already runs
  the attempt→iteration→workflow `Cancelled` sequence; a working template for
  `cancel_attempt`.
- **`close_attempt` / `close_workflow` / `cancellation_outcomes` /
  `WorkflowHandleRegistry`** — orchestration helpers, unchanged.
- **`SubagentRecord` / `WorkflowBackgroundRecord` / `CommandSessionCancelTarget`**
  — supervisor bookkeeping structs, kept (per-run supervisor owns them locally).
- **`reaper.rs` / `RunHost` / `Disposition::Cancelled`** — backend finalize path.
- **`AgentRunStore::get_for_task`** — persisted task→run fallback when no live run.
- **`run_agent` / `run_advisor`** — reused, modified only to thread the token.
- **`JoinSet::abort_all`** in `dispatch_many_foreground_tools` — reused as the
  foreground tool-cancel mechanism.

## 6. State / store changes (gating — break exhaustive matches)

| Add variant | Then update |
|---|---|
| `TaskStatus::Cancelled` (`eos-state/src/task.rs:17`) | `is_terminal_generator()`; reachability `matches!` at `plan_dag.rs:50` (decide: `Cancelled` blocks the DAG — **yes**) |
| `AttemptStatus::Cancelled` + `AttemptClosure::Cancelled { reason, outcomes, closed_at }` (+ `status()`) (`eos-state/src/attempt.rs`) | exhaustive match in `attempt_state_from_columns` (`eos-db/src/rows.rs:437`); `SqlAttemptStore::close` fail-reason extraction (`attempt.rs:125`) |
| `RequestStatus::Cancelled` (`eos-state/src/request.rs:11`) | `is_terminal()`; `reconcile()` in the detail handler (`user_requests.rs:98`) |
| *(none)* `IterationStatus::Cancelled` / `WorkflowStatus::Cancelled` | already exist — reuse |

`terminal_tool_result` on a cancelled `Task`: stamp `{ "fail_reason": "cancelled",
"reason": <reason> }` for parity with the existing `workflow_cancelled` marker;
iteration/workflow `outcomes` columns stay the empty typed projection `[]`.

## 7. Decisions & sharp edges

1. **Backend stops dropping the future.** `launcher.cancel` / `run_to_completion`
   **await `cancel_request`**; the agent-core token makes `run_request`
   self-terminate and return `Disposition::Cancelled`. The `RunSlot` /
   biased-`select!` may then be simplified or removed. This is the change that
   makes agent-core request state consistent on cancel.
2. **Token granularity tradeoff.** Cancel takes effect at query-loop turn
   boundaries (provider stream not cancel-safe). Dropping the planner/subagent
   `AbortHandle` side-maps trades immediate abort for clean turn-boundary
   teardown. Keep `abort_all` for in-flight foreground tools (immediate is fine).
3. **Heartbeat must go registry-aware.** `spawn_command_completion_heartbeat`
   (`heartbeat.rs:37`) is request-scoped over the single supervisor today; with
   per-run supervisors it must iterate `AgentRunRegistry` (or be per-run). This is
   a required modify, not a drop — the one non-obvious ripple of per-run
   supervisors.
4. **Idempotency = CAS + registry presence** (replaces `precedence` and `armed`).
   A second `cancel_*` call no-ops: the status CAS fails (already `Cancelled`)
   and/or the run is absent from the registry.
5. **Latch covers all three task kinds.** `cancel_attempt` must latch
   `planner_task_id ∪ generator_task_ids ∪ reducer_task_ids` before any
   `cancel_task`, or a planner still mid-run could finish and the scheduler could
   launch a not-yet-latched generator.
6. **No parent mutation.** `cancel_workflow` must not touch the parent task
   (existing invariant GC-eos-workflow-01/02).

## 8. Crate dependency / dispatch

- `CancelPort` and `WorkflowControlPort` both live in `eos-tools` (the shared port
  crate). `eos-engine` implements `CancelPort`; `eos-workflow` implements
  `WorkflowControlPort`. `eos-runtime` wires both `dyn` ports together at the
  composition root. No cross-crate back-edge; recursion ping-pongs across the two
  ports exactly as `engine ↔ workflow` already communicate.
- `CancellationToken`: use `tokio_util::sync::CancellationToken`; nested
  foreground runs (advisor) get `token.child_token()`.

## 9. Implementation phases & verification ladder

1. **State variants** — add `TaskStatus::Cancelled`, `AttemptStatus`/
   `AttemptClosure::Cancelled`, `RequestStatus::Cancelled`; fix exhaustive matches.
   Verify: `cargo check -p eos-state -p eos-db --all-targets`.
2. **Registry + token** — `AgentRunRegistry`, `AgentRunControl`, token field in
   `AgentRunInput`/`QueryContext`, `select!`/turn-boundary check in `run_query`,
   `QueryExitReason::Cancelled`. Verify: `cargo test -p eos-engine`.
3. **Primitives** — `CancelPort`, `cancel_task`, `cancel_agent_run`,
   `BackgroundSupervisor::cancel`; drop `BackgroundRunFinalizer` and
   `cancel_for_parent_exit`; per-run supervisor; advisor `tool_children`.
   Verify: `cargo test -p eos-engine` (incl. a cancel-finalizes-records test).
4. **Workflow decomposition** — `cancel_workflow/iteration/attempt`; drop
   `cancel_workflow_state` / `cancel_active_task` / `abort_planner` /
   `store_planner_abort_with`; `latch_attempt_tasks_cancelled`. Verify:
   `cargo test -p eos-workflow` (incl. a nested-delegation cancel test).
5. **Request + backend wiring** — `cancel_request`; drop request-scoped supervisor
   + entry cleanup tail; launcher awaits `cancel_request`; reconcile handler.
   Verify: `cargo test -p eos-runtime`, backend launcher tests, then
   `cargo clippy --workspace --all-targets -- -D warnings`.
6. **Heartbeat** — registry-aware. Verify: command-session completion tests.

### Success criteria

- A cancel at any nesting depth leaves **every** `Task`, `Attempt`, `Iteration`,
  `Workflow`, `agent_run`, and message record in a terminal state — no open rows.
- Cancellation is fully awaited: when `cancel_request` returns, no detached
  cleanup remains in flight.
- No `Drop`-based or `tokio::spawn` fire-and-forget in any cancel path.
- Calling cancel twice is a no-op (idempotent).
