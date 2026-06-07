# Backend-Server Cancellation Wiring — SPEC

Status: Proposed
Date: 2026-06-07
Owner: backend-server runtime
Scope: `backend-server/crates/eos-backend-api`,
`backend-server/crates/eos-backend-runtime`,
`backend-server/crates/eos-backend-store`,
`backend-server/crates/eos-backend-types`
Related:
- Agent-core cancellation:
  `docs/plans/agent_run_local_background_supervisor_SPEC.md`
- Sandbox cancellation substrate:
  `docs/plans/daemon_workspace_run_registry_SPEC.md`

## 1. Purpose

This is the third cancellation document. It wires the two lower-level
cancellation owners together:

- agent-core owns task/agent-run/workflow cancellation,
- sandbox owns workspace-run/session teardown and shared-workspace commit gates,
- backend-server owns API-level orchestration, run metadata, event-stream
  finalization, and sandbox release ordering.

Backend-server must not implement agent-core recursion and must not implement
sandbox workspace teardown. It calls those boundaries in order and records the
backend lifecycle outcome.

## 2. Current Stale Behavior

The live backend-server launcher currently treats cancellation as backend-local:

- `RunLauncher::cancel` stores a reason and fires a local cancellation token.
- The run task races the `run_request` future against that token.
- If the token wins, the run future is dropped.
- The backend finalizer releases the sandbox and writes `run_meta.cancelled`.
- Backend never writes `cancelled` into agent-core state.

That behavior was correct for the previous backend-local cancellation model, but
it is stale once agent-core has an explicit cancellation entry point and sandbox
has an explicit workspace-run cancel/commit gate.

Target behavior:

- Backend cancellation is not "drop the agent-core future".
- Backend cancellation calls `cancel_agent_core_user_request`.
- Backend cancellation calls `cancel_sandbox_user_request`.
- Backend releases/destroys the sandbox only after sandbox cancellation and
  commit gating returns.
- Backend writes `run_meta.cancelled` only after the lower-level cancellation
  boundaries have been awaited or have returned an idempotent no-op.

## 3. Goals

- Preserve backend-server as the production composition layer.
- Keep cancellation boundaries explicit:
  - agent-core request state and live runs are cancelled by agent-core,
  - sandbox workspace/session resources are cancelled by sandbox,
  - backend-server coordinates both and records API lifecycle state.
- Avoid dropping `eos_runtime::run_request` as the cancellation mechanism.
- Make cancellation awaited end-to-end from the API handler's perspective:
  `DELETE` may return once the request is accepted for cancellation, but one
  backend-owned cancellation task must await all lower-level cleanup before final
  run metadata is terminal.
- Keep `run_meta` authoritative for backend API lifecycle.
- Add `RequestStatus::Cancelled` support to status resolution once agent-core
  owns cancelled request state.
- Keep sandbox destruction after sandbox cancellation/commit, never before.

## 4. Non-Goals

- No agent-core cancellation implementation in backend-server.
- No sandbox daemon workspace-run implementation in backend-server.
- No direct backend writes into agent-core stores.
- No HTTP exposure of daemon endpoints, auth tokens, or internal sandbox ports.
- No peer-to-peer agent communication.
- No backend-owned retry of individual tool cancellations; lower layers own
  their idempotency.

## 5. Target File and Folder Structure

```text
backend-server/crates/eos-backend-api/src/
  handlers/
    user_requests.rs       # DELETE delegates to RunLauncher::cancel

backend-server/crates/eos-backend-runtime/src/
  cancellation.rs          # new: BackendCancellationCoordinator and ports
  launcher.rs              # RunSlot state and cancellation scheduling
  finalizer.rs             # RunFinalizer terminal run_meta/event finalization
  sandbox_manager.rs       # implements SandboxCancellationPort or adapter source
  host.rs                  # RunHost may expose agent-core cancellation adapter
  status.rs                # RequestStatus::Cancelled resolution

backend-server/crates/eos-backend-store/src/
  run_meta.rs              # terminal cancelled write remains backend-owned

backend-server/crates/eos-backend-types/src/
  requests.rs              # cancel response DTOs if API surface expands
```

## 6. Object Model

### 6.1 BackendCancellationCoordinator

The coordinator owns the ordered cancel sequence for one user request.

```rust
pub struct BackendCancellationCoordinator {
    agent_core: Arc<dyn AgentCoreCancellationPort>,
    sandbox: Arc<dyn SandboxCancellationPort>,
    finalizer: RunFinalizer,
}
```

Methods:

```rust
impl BackendCancellationCoordinator {
    pub async fn cancel_user_request(
        &self,
        plan: BackendCancelPlan,
    ) -> Result<BackendCancelReport, BackendCancelError>;
}
```

Rules:

- It is the only backend runtime object that calls both agent-core cancellation
  and sandbox cancellation.
- It awaits agent-core cancellation before sandbox cancellation.
- It calls the finalizer only after sandbox cancellation returns.
- It treats missing lower-level state as an idempotent no-op when the backend run
  is already in a terminal or pre-bootstrap phase.
- It returns one compact report. Per-tool, per-session, and per-workflow errors
  from lower layers are collapsed into layer summaries so the API is not flooded
  with repeated cleanup errors.

### 6.2 BackendCancelPlan

The immutable cancellation input assembled from `RunSlot` and
`SandboxManager`.

```rust
pub struct BackendCancelPlan {
    pub request_id: RequestId,
    pub sandbox_id: Option<SandboxId>,
    pub reason: String,
    pub agent_core_started: bool,
}
```

Rules:

- `sandbox_id` is `None` before sandbox acquisition has completed.
- `agent_core_started` is `false` before `RunHost::run` has entered
  `eos_runtime::run_request`.
- Cancellation before those phases should still finalize `run_meta.cancelled`
  but skip the missing lower-level calls.

### 6.3 BackendCancelReport

```rust
pub struct BackendCancelReport {
    pub request_id: RequestId,
    pub agent_core: CancelLayerOutcome,
    pub sandbox: CancelLayerOutcome,
    pub run_meta_finalized: bool,
    pub primary_error: Option<BackendCancelError>,
}

pub enum CancelLayerOutcome {
    Skipped { reason: CancelSkipReason },
    Completed,
    NoOp { reason: CancelNoOpReason },
    Failed { summary: String },
}

pub enum CancelSkipReason {
    AgentCoreNotStarted,
    SandboxNotBound,
}

pub enum CancelNoOpReason {
    AlreadyCancelled,
    AlreadyFinished,
    NotFound,
}
```

Report rules:

- `AlreadyCancelled`, `AlreadyFinished`, and missing lower-level state normalize
  to `NoOp`, not hard failure.
- Agent-core failure does not suppress sandbox cancellation when a sandbox is
  bound. Sandbox cleanup is the authoritative orphan/resource backstop.
- `primary_error` is at most one selected failure summary, chosen in boundary
  order: agent-core first, then sandbox, then backend finalization.

### 6.4 AgentCoreCancellationPort

Backend-facing adapter over the agent-core cancellation entry point.

```rust
#[async_trait]
pub trait AgentCoreCancellationPort: Send + Sync {
    async fn cancel_agent_core_user_request(
        &self,
        request_id: &RequestId,
        reason: &str,
    ) -> Result<(), BackendCancelError>;
}
```

The production adapter calls:

```rust
eos_runtime::cancel_agent_core_user_request(...)
```

Agent-core owns:

- `RequestStatus::Cancelled`,
- `TaskStatus::Cancelled`,
- `cancel_task`,
- `cancel_agent_run`,
- workflow/iteration/attempt cancellation decomposition,
- live `AgentRunControl` teardown.

### 6.5 SandboxCancellationPort

Backend-facing adapter over sandbox cancellation and commit gating.

```rust
#[async_trait]
pub trait SandboxCancellationPort: Send + Sync {
    async fn cancel_sandbox_user_request(
        &self,
        sandbox_id: &SandboxId,
        reason: &str,
    ) -> Result<SandboxCancelReport, BackendCancelError>;
}
```

The production adapter is owned by backend runtime / sandbox manager and calls
the sandbox boundary described by `daemon_workspace_run_registry_SPEC.md`.

Sandbox owns:

- `cancel_all_workspace_runs`,
- command-session teardown,
- isolated workspace teardown,
- orphan resource cleanup,
- active lease gate,
- `commit_to_workspace`,
- daemon readiness after cleanup.

### 6.6 RunSlot

`RunSlot` should become stateful enough to distinguish scheduling state from
actual cancellation.

```rust
struct RunSlot {
    phase: Mutex<RunPhase>,
    wake_cancel: CancellationToken,
    reason: Mutex<Option<String>>,
}

enum RunPhase {
    Accepted,
    Provisioning,
    Running {
        sandbox_id: SandboxId,
        agent_core_started: bool,
    },
    Cancelling {
        sandbox_id: Option<SandboxId>,
        agent_core_started: bool,
    },
    Finished,
}
```

Rules:

- `wake_cancel` is only a scheduler signal to the launcher task.
- It is not the cancellation mechanism for agent-core or sandbox.
- The cancellation mechanism is `BackendCancellationCoordinator`.

## 7. Target Flow

### 7.1 DELETE API

```text
DELETE /api/user-requests/{request_id}
  ├─ read run_meta
  ├─ if no row: 404
  ├─ RunLauncher::cancel(request_id, reason)
  │    ├─ atomically mark RunSlot as Cancelling if not Finished
  │    ├─ store reason
  │    └─ wake launcher task
  └─ return 202 Accepted
```

`409 Conflict` remains valid for a known run whose slot is already `Finished`.

### 7.2 Launcher Task

```text
run_to_completion
  ├─ write run_meta(Accepted) before spawn
  ├─ acquire sandbox to completion
  ├─ mark RunPhase::Running { sandbox_id, agent_core_started: false }
  ├─ start agent-core run
  ├─ mark agent_core_started = true
  ├─ race:
  │    ├─ agent-core run returns Done/Failed
  │    └─ RunSlot wakes with Cancelling
  ├─ if Done/Failed wins:
  │    └─ RunFinalizer::finalize_terminal(Done/Failed) -> normal sandbox release
  └─ if Cancelling wins:
       └─ BackendCancellationCoordinator::cancel_user_request(plan)
```

Provisioning remains non-cancellable until the binding is recorded. A cancel
request during provisioning marks the slot as `Cancelling`; after acquire records
the sandbox binding, the launcher immediately runs the cancellation coordinator
instead of starting agent-core.

### 7.3 Coordinator Sequence

```text
BackendCancellationCoordinator::cancel_user_request(plan)
  ├─ if plan.agent_core_started:
  │    └─ agent_core.cancel_agent_core_user_request(request_id, reason)
  │         -> normalize to CancelLayerOutcome
  ├─ if plan.sandbox_id.is_some():
  │    └─ sandbox.cancel_sandbox_user_request(sandbox_id, reason)
  │         -> normalize to CancelLayerOutcome
  ├─ finalizer.finalize_cancelled_after_cleanup(request_id, reason)
  └─ mark RunSlot::Finished
```

Ordering is intentional:

1. Agent-core cancellation promptly stops live agent runs and tool-spawned
   effects by owner.
2. Sandbox cancellation is authoritative and re-enumerates sandbox resources even
   if agent-core was interrupted.
3. `RunFinalizer` terminalizes backend state and releases/destroys the sandbox
   only after sandbox cancellation has returned.

The coordinator remains sequential by design. Parallel lower-layer cancellation
would shorten latency, but it can produce noisy overlapping errors and harder
diagnostics. The preferred user-facing behavior is one ordered result with one
selected primary error.

## 8. RunFinalizer Changes

Current backend finalization releases the sandbox first for every disposition.
That is stale for cancellation because sandbox release can destroy the container
before the sandbox cancellation/commit gate runs.

Target:

```rust
impl RunFinalizer {
    pub async fn finalize_terminal(
        &self,
        request_id: &RequestId,
        disposition: Disposition,
    );

    pub async fn finalize_cancelled_after_cleanup(
        &self,
        request_id: &RequestId,
        reason: Option<&str>,
    );
}
```

Rules:

- `Done` / `Failed`: normal release path is unchanged.
- `Cancelled`: cancellation coordinator already ran agent-core and sandbox
  cleanup; finalizer may then release the sandbox ref and write
  `run_meta.cancelled`.
- `run_meta.cancelled` write remains retried because backend cancelled status is
  API-authoritative.
- `event_bus.finish(request_id)` runs after terminal metadata is attempted.
- `RunFinalizer` is not a sandbox cleanup owner. It remains after the sandbox
  migration because backend-server still owns terminal `run_meta`, event stream
  closure, and sandbox reference release ordering.

## 9. Status Resolution

Once agent-core adds `RequestStatus::Cancelled`, backend status resolution must
include it.

Target precedence:

| Backend status | Agent-core status | API status |
| --- | --- | --- |
| `Cancelled` | any | `cancelled` |
| `Failed` | any | `failed` |
| `Done` | any | `done` |
| `Running` / `Accepted` | `Cancelled` | `cancelled` |
| `Running` / `Accepted` | `Failed` | `failed` |
| `Running` / `Accepted` | `Done` | `done` |
| `Running` / `Accepted` | `Running` | `running` |
| `Running` | missing | `running` |
| `Accepted` | missing | `accepted` |

Rules:

- Backend terminal state still wins.
- Non-terminal backend state defers to agent-core terminal state.
- Reconcile may persist `Cancelled` onto `run_meta` only through a CAS guard,
  same as `Done` / `Failed`.

## 10. Boundary Contracts

### 10.1 Agent-Core Boundary

Backend-server calls:

```rust
cancel_agent_core_user_request(request_id, reason)
```

Expected lower-level result:

- no live agent run remains for the request,
- root task/request state is terminal `Cancelled`,
- task/workflow/attempt state is recursively cancelled,
- agent-run and message records are terminal,
- tool-spawned effects were asked to teardown through their owners.

Backend-server does not inspect the recursion tree. It trusts the returned
result or error.

### 10.2 Sandbox Boundary

Backend-server calls:

```rust
cancel_sandbox_user_request(sandbox_id, reason)
```

Expected lower-level result:

- all workspace runs in that sandbox are cancelled,
- command sessions are gone,
- isolated sessions are exited and orphan resources are cleaned up,
- active leases are zero,
- shared LayerStack is committed through the sandbox gate,
- the daemon remains ready.

Backend-server does not enumerate command sessions or isolated handles itself.

## 11. Current-to-Target Changes

| Current | Target |
| --- | --- |
| `RunLauncher::cancel` only fires a local token | it marks `RunSlot::Cancelling` and wakes the launcher task |
| `tokio::select!` drops `run_request` on cancel | cancellation coordinator awaits agent-core cancellation |
| backend never writes agent-core `Cancelled` | backend calls agent-core; agent-core owns `RequestStatus::Cancelled` |
| backend finalizer releases sandbox before cancelled cleanup | coordinator runs sandbox cleanup before `RunFinalizer` releases |
| status resolver ignores `RequestStatus::Cancelled` | status resolver maps it to API `cancelled` |
| sandbox release is the only cancel-time sandbox action | sandbox cancellation boundary runs cancel-all + commit gate first |

## 12. Implementation Phases

### Phase 1: Ports and Coordinator Skeleton

- Add `cancellation.rs`.
- Add `AgentCoreCancellationPort`.
- Add `SandboxCancellationPort`.
- Add `BackendCancellationCoordinator`.
- Wire fakes in backend runtime tests.

### Phase 2: RunSlot State Machine

- Replace token-only cancellation state with `RunPhase`.
- Keep the token only as a wake signal.
- Preserve non-cancellable acquisition until a binding is recorded.

### Phase 3: RunFinalizer Ordering

- Rename the backend finalization object to `RunFinalizer`.
- Split normal terminal finalization from cancelled-after-cleanup finalization.
- Ensure cancelled sandbox release happens after sandbox cleanup returns.

### Phase 4: API and Status Resolution

- Keep `DELETE` returning `202` after cancellation is accepted.
- Add `RequestStatus::Cancelled` mapping to status resolution.
- Update reconcile CAS to handle agent-core cancelled state.

### Phase 5: Production Adapters

- Runtime host implements `AgentCoreCancellationPort` by calling
  `eos_runtime::cancel_agent_core_user_request`.
- Sandbox manager or host adapter implements `SandboxCancellationPort` by calling
  the sandbox cancellation boundary.

## 13. Required Tests

### Launcher

- Cancel while accepted but before provisioning finalizes results in
  `run_meta.cancelled` and no agent-core cancel call.
- Cancel while provisioning waits for acquisition, then runs sandbox cancellation
  before release.
- Cancel while agent-core is running calls agent-core cancellation, then sandbox
  cancellation, then `RunFinalizer`.
- Completion racing cancellation is deterministic:
  - terminal completion wins if the slot was already finished,
  - cancellation wins if the slot was marked `Cancelling` first.

### RunFinalizer

- `Done` / `Failed` still release normally.
- `Cancelled` does not release before sandbox cancellation has been observed.
- Failed `run_meta.cancelled` write is retried.

### Status

- `RequestStatus::Cancelled` maps to API `cancelled` when backend is
  non-terminal.
- Backend terminal statuses still override agent-core status.

### API

- `DELETE /api/user-requests/{id}` returns `202` when cancellation is accepted.
- `DELETE` returns `409` for a known finished run.
- `DELETE` returns `404` for an unknown run.

## 14. Verification Commands

```sh
(cd backend-server && cargo test -p eos-backend-runtime launcher)
(cd backend-server && cargo test -p eos-backend-runtime finalizer)
(cd backend-server && cargo test -p eos-backend-runtime status)
(cd backend-server && cargo test -p eos-backend-api api_contract)
(cd backend-server && cargo clippy -p eos-backend-runtime -p eos-backend-api --all-targets -- -D warnings)
```

## 15. Acceptance Criteria

- Backend cancellation no longer relies on dropping `run_request`.
- Backend calls the agent-core cancellation boundary when agent-core has started.
- Backend calls the sandbox cancellation boundary when a sandbox is bound.
- Backend releases/destroys the sandbox only after sandbox cancellation returns.
- Backend records terminal `run_meta.cancelled` with `cancel_reason`.
- API status resolution understands agent-core `RequestStatus::Cancelled`.
- The backend spec remains a wiring document; agent-core and sandbox details stay
  in their owning specs.
