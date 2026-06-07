# Agent-Run Local Runtime and Cancellation — SPEC

Status: Proposed
Date: 2026-06-07
Owner: agent-core runtime / engine
Scope: `eos-runtime`, `eos-engine`, `eos-workflow`, `eos-state`,
`eos-db`, `eos-tools`
Supersedes: the agent-core portions of
`docs/plans/uniform_recursive_cancellation_SPEC.md`
Related:
- `docs/plans/daemon_workspace_run_registry_SPEC.md` for sandbox-owned
  workspace-run cancellation and commit gating.
- `docs/plans/backend_server_cancellation_wiring_SPEC.md` for backend-server's
  API-level cancellation coordinator.

## 1. Problem

The current Rust runtime wires one `BackgroundSupervisorHandle`, one
`NotificationService`, and one command-completion heartbeat at request scope.
The records are filtered by `agent_run_id`, but the owning objects are still
shared by all root and workflow agent runs in the request.

That is the wrong ownership boundary. `agent_run_id` filtering is a data filter,
not object ownership. If two workflow agents are active at the same time, a
shared notification queue can let one agent loop drain another agent's command
completion notification.

Cancellation has the same ownership problem. Current cancellation is split across
future dropping, `BackgroundRunFinalizer::Drop`, request-wide supervisor sweeps,
subagent `AbortHandle` side maps, workflow-specific cancellation helpers, and
sandbox command-session cancellation. That makes hard cancellation non-uniform:
agent-run/message records can remain open, cleanup can be fire-and-forget, and a
request-level sweep can cancel work that should be owned by a specific agent run.

The target design is agent-run local management:

- each agent run owns its own `AgentRunControl`,
- each agent run owns its own `StopSignal`,
- each agent run owns its own lightweight foreground executor,
- each agent run owns its own `BackgroundSupervisorHandle`,
- each background supervisor owns its own notification manager,
- each background supervisor owns its own command-completion heartbeat runner,
- workflows, command sessions, and subagents all use the same record pattern:
  `handle + status + result/progress metadata`,
- cancellation reduces to two recursive agent-core primitives:
  `cancel_task(task_id, reason)` and `cancel_agent_run(agent_run_id, reason)`.

## 2. Goals

- Move mutable background state from request scope to agent-run scope.
- Make `BackgroundSupervisorHandle` the object-oriented owner of:
  - the per-agent background ledger,
  - the per-agent `NotificationService`,
  - the per-agent command-completion heartbeat.
- Add `AgentRunControl` as the object-oriented owner of one live agent run:
  `StopSignal`, foreground executor, background supervisor, and finalization
  handles.
- Add `AgentRunRegistry` so live runs and task-to-run ownership are addressable
  without backend future dropping.
- Replace request-wide cleanup with explicit, awaited cancellation.
- Use a consistent lane pattern for workflows, command sessions, and subagents:
  every record contains a first-class handle object.
- Remove in-memory `agent_run_id` filtering from background records once the
  supervisor itself is per-agent.
- Make command sessions cancelable from creation, not only after
  `yield_time_ms`.
- Keep request/workflow composition shared only where it is truly shared:
  stores, workflow control, attempt registries, agent registry, tool config,
  sandbox transport, and immutable engine handles.
- Keep Rust object-oriented design idiomatic:
  concrete structs own state, trait objects expose runtime-selected ports, and
  lifecycle is explicit through methods and awaited teardown.

## 3. Non-Goals

- No backend-server API behavior in this spec. Backend-server calls the
  agent-core cancellation entry point; it does not own agent-core recursion.
- No sandbox daemon cancel-all / commit implementation in this spec. Sandbox
  cleanup belongs to the sandbox cancellation substrate spec.
- No daemon command-session protocol redesign beyond the port calls required by
  cancelable resources.
- No global request-level agent orchestrator.
- No inheritance-style trait hierarchy.
- No broad service bag that recreates request-global mutable state.
- No peer-to-peer agent communication.
- No change to the sandbox identity contract: sandbox wire calls still use
  `caller_id`, and agent-core still uses typed `AgentRunId`.

## 4. Ownership Model

```text
Request runtime
  owns shared factories and workflow composition only
  ├─ BackgroundSupervisorFactory
  ├─ WorkflowControlPort
  ├─ AttemptSubmissionPort
  ├─ CancelPort
  ├─ stores / registries / transport
  └─ RuntimeAgentRunner

Agent run
  owns one AgentRunControl
    ├─ agent_run_id
    ├─ task_id
    ├─ StopSignal
    ├─ ForegroundExecutor
    ├─ BackgroundSupervisorHandle
    │    ├─ owner_agent_run_id
    │    ├─ NotificationService
    │    ├─ CommandCompletionHeartbeat
    │    └─ BackgroundTaskSupervisor
    │         ├─ SubagentLane
    │         ├─ WorkflowLane
    │         └─ CommandSessionLane
    └─ finalization handles
```

The request may create factories once, but it must not own mutable per-agent
background records, foreground effects, stop signals, or notification queues.

## 5. Target File and Folder Structure

```text
agent-core/crates/eos-engine/src/
  runtime/
    agent_loop.rs
    cancel.rs              # new: CancelPort implementation
    control.rs             # new: AgentRunControl, StopSignal
    foreground.rs          # new: ForegroundExecutor
    registry.rs            # new: AgentRunRegistry
    setup.rs
    types.rs
  background/
    mod.rs
    factory.rs             # new: builds one supervisor handle per agent run
    handle.rs              # BackgroundSupervisorHandle and runtime owner
    heartbeat.rs           # CommandCompletionHeartbeat RAII runner
    supervisor.rs          # BackgroundTaskSupervisor lane container
    lanes/
      mod.rs
      subagent.rs          # SubagentLane, SubagentHandle, SubagentRecord
      workflow.rs          # WorkflowLane, WorkflowHandle, WorkflowBackgroundRecord
      command_session.rs   # CommandSessionLane, CommandSessionHandle, CommandSessionRecord
    subagent.rs            # BackgroundSupervisorPort implementation
    command_session.rs     # CommandSessionSupervisorPort implementation

agent-core/crates/eos-runtime/src/
  cancel.rs                # new: cancel_agent_core_user_request
  entry.rs                 # root agent creates one AgentRunControl
  agent_runner.rs          # each workflow agent run creates one AgentRunControl
  runtime_services/
    engine.rs              # completion poll interval remains config-backed

agent-core/crates/eos-tools/src/
  ports/mod.rs             # CancelPort, CancelableResource, per-agent ports
  tools/sandbox/
    exec_command.rs
    write_stdin.rs
    read_command_progress.rs
  tools/workflow/
    delegate_workflow.rs
    cancel_workflow.rs
  tools/subagent/
    run_subagent.rs
    check_subagent_progress.rs
    cancel_subagent.rs

agent-core/crates/eos-workflow/src/
  cancel.rs                # new: cancel_workflow/cancel_iteration/cancel_attempt
  ports.rs                 # WorkflowControlPort integration

agent-core/crates/eos-state/src/
  request.rs               # RequestStatus::Cancelled
  task.rs                  # TaskStatus::Cancelled
  attempt.rs               # AttemptStatus/AttemptClosure::Cancelled

agent-core/crates/eos-db/src/
  stores/                  # cancelled-state persistence and latch methods
```

The `background/lanes/` split is optional during a small first patch, but it is
the target shape. If the first implementation keeps existing files, the final
type names and fields below still apply.

## 6. Core Runtime Classes and Fields

### 6.1 StopSignal

The cooperative half of cancellation. The query loop polls it at turn
boundaries. Provider streams are not treated as cancel-safe; do not interrupt a
provider stream mid-token unless a later provider contract explicitly supports
that.

```rust
#[derive(Clone)]
pub struct StopSignal {
    token: CancellationToken,
    reason: Arc<Mutex<Option<String>>>,
}
```

Methods:

```rust
impl StopSignal {
    pub fn new() -> Self;
    pub fn request(&self, reason: impl Into<String>);
    pub fn is_requested(&self) -> bool;
    pub fn reason(&self) -> Option<String>;
    pub async fn requested(&self);
    pub fn child(&self) -> StopSignal;
}
```

Rules:

- `StopSignal` stops future work.
- It does not clean up already-spawned effects.
- Cleanup of spawned effects is owned by `CancelableResource::teardown`.

### 6.2 AgentRunControl

The live object for one agent run.

```rust
pub struct AgentRunControl {
    agent_run_id: AgentRunId,
    task_id: Option<TaskId>,
    stop: StopSignal,
    foreground: ForegroundExecutor,
    background: BackgroundSupervisorHandle,
    finalization: AgentRunFinalization,
}
```

Finalization data:

```rust
pub struct AgentRunFinalization {
    persist_agent_run: bool,
    message_record: Mutex<Option<AgentRunRecordHandle>>,
}
```

Methods:

```rust
impl AgentRunControl {
    pub fn agent_run_id(&self) -> &AgentRunId;
    pub fn task_id(&self) -> Option<&TaskId>;
    pub fn stop(&self) -> StopSignal;
    pub fn background(&self) -> BackgroundSupervisorHandle;
    pub fn notifications(&self) -> NotificationService;

    pub async fn teardown(&self, reason: &str) -> Result<BackgroundInflightReport, EngineError>;
    pub async fn finish_cancelled(&self, reason: &str) -> Result<(), EngineError>;
}
```

Rules:

- `AgentRunControl` is registered before the provider loop starts.
- It is removed only after terminal finalization or explicit cancellation
  finalization completes.
- It replaces `BackgroundRunFinalizer::Drop` as the cleanup owner.
- Cleanup is awaited; `Drop` may log if armed but must not be the normal cleanup
  mechanism.

### 6.3 AgentRunRegistry

Live address book for recursive cancellation.

```rust
#[derive(Clone)]
pub struct AgentRunRegistry {
    inner: Arc<Mutex<AgentRunRegistryState>>,
}

struct AgentRunRegistryState {
    by_run_id: HashMap<AgentRunId, Arc<AgentRunControl>>,
    by_task_id: HashMap<TaskId, AgentRunId>,
}
```

Methods:

```rust
impl AgentRunRegistry {
    pub fn insert(&self, control: Arc<AgentRunControl>);
    pub fn get(&self, agent_run_id: &AgentRunId) -> Option<Arc<AgentRunControl>>;
    pub fn agent_run_for_task(&self, task_id: &TaskId) -> Option<AgentRunId>;
    pub fn remove(&self, agent_run_id: &AgentRunId);
}
```

Rules:

- Registry presence is part of cancellation idempotency.
- A missing run means it already finished or was never live in this process.
- Persisted `AgentRunStore::get_for_task` may be used as a fallback for reporting,
  but live teardown uses this registry.

### 6.4 ForegroundExecutor

Foreground work is awaited inline by the query loop. It does not need records,
heartbeat, progress delivery, or notification latches. It only needs
cancel-reachability.

```rust
pub struct ForegroundExecutor {
    resources: Mutex<HashMap<ForegroundResourceId, Arc<dyn CancelableResource>>>,
    inline_agent_runs: Mutex<HashMap<AgentRunId, InlineAgentRunHandle>>,
}

pub struct ForegroundResourceId(String);

pub struct InlineAgentRunHandle {
    agent_run_id: AgentRunId,
}
```

Methods:

```rust
impl ForegroundExecutor {
    pub fn register_resource(
        &self,
        id: ForegroundResourceId,
        resource: Arc<dyn CancelableResource>,
    );

    pub fn unregister_resource(&self, id: &ForegroundResourceId);

    pub fn register_inline_agent_run(&self, agent_run_id: AgentRunId);

    pub async fn teardown(
        &self,
        cancel_port: &dyn CancelPort,
        reason: &str,
    ) -> Result<(), ToolError>;
}
```

Rules:

- The existing foreground `JoinSet` remains the execution substrate.
- `ForegroundExecutor` is not a mirror supervisor.
- `ask_advisor` registers an inline child agent run; teardown calls
  `cancel_agent_run(child)`.
- `exec_command` registers a daemon invocation cancel resource before the
  sandbox RPC starts. If the daemon returns a running `command_session_id`, the
  resource is upgraded or re-registered as a `CommandSessionHandle` in the
  background lane.

## 7. Shared Cancellation Ports

### 7.1 CancelableResource

Every non-leaf effect a tool creates supplies a teardown.

```rust
#[async_trait]
pub trait CancelableResource: Send + Sync {
    async fn teardown(&self, reason: &str) -> Result<(), ToolError>;
}
```

Implementations:

| Resource | Teardown |
| --- | --- |
| `CommandInvocationHandle` | `api.v1.cancel` by sandbox invocation id |
| `CommandSessionHandle` | `api.v1.command.cancel` by `command_session_id` |
| `WorkflowHandle` | `WorkflowControlPort::cancel(workflow_task_id, reason)` |
| `SubagentHandle` | `CancelPort::cancel_agent_run(child_agent_run_id)`, then `driver_abort.abort()` as a backstop |
| `InlineAgentRunHandle` | `CancelPort::cancel_agent_run(agent_run_id)` |

### 7.2 CancelPort

The two recursive agent-core cancellation primitives.

```rust
#[async_trait]
pub trait CancelPort: Send + Sync {
    async fn cancel_task(&self, task_id: &TaskId, reason: &str) -> Result<(), ToolError>;

    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), ToolError>;
}
```

Home:

- trait: `eos-tools/src/ports/mod.rs`,
- implementation: `eos-engine/src/runtime/cancel.rs`,
- runtime entry: `eos-runtime/src/cancel.rs`.

This avoids an `eos-engine` <-> `eos-workflow` crate cycle while preserving the
recursive cancellation graph.

## 8. Background Supervisor Classes and Fields

### 8.1 BackgroundSupervisorFactory

Owned by request/workspace composition. It is immutable and cheap to clone. It
creates per-agent supervisor handles.

```rust
pub struct BackgroundSupervisorFactory {
    handles: EngineRunHandles,
    transport: Arc<dyn SandboxTransport>,
    completion_poll_interval: Duration,
}
```

Methods:

```rust
impl BackgroundSupervisorFactory {
    pub fn new(
        handles: EngineRunHandles,
        transport: Arc<dyn SandboxTransport>,
        completion_poll_interval: Duration,
    ) -> Self;

    pub fn create(&self, owner_agent_run_id: AgentRunId) -> BackgroundSupervisorHandle;
}
```

Rules:

- The factory contains no mutable per-agent ledger.
- `RuntimeAgentRunner` may store `Arc<BackgroundSupervisorFactory>`.
- The root path in `entry.rs` uses the same factory to create the root agent's
  local supervisor.

### 8.2 BackgroundSupervisorHandle

The background object for one agent run.

```rust
#[derive(Clone)]
pub struct BackgroundSupervisorHandle {
    runtime: Arc<BackgroundSupervisorRuntime>,
}

struct BackgroundSupervisorRuntime {
    owner_agent_run_id: AgentRunId,
    inner: Arc<Mutex<BackgroundTaskSupervisor>>,
    handles: EngineRunHandles,
    transport: Arc<dyn SandboxTransport>,
    completion_poll_interval: Duration,
    notifications: NotificationService,
    completion_heartbeat: CommandCompletionHeartbeat,
}
```

Methods:

```rust
impl BackgroundSupervisorHandle {
    pub fn new(
        owner_agent_run_id: AgentRunId,
        handles: EngineRunHandles,
        transport: Arc<dyn SandboxTransport>,
        completion_poll_interval: Duration,
    ) -> Self;

    pub fn owner_agent_run_id(&self) -> &AgentRunId;
    pub fn notifications(&self) -> NotificationService;
    pub fn inner(&self) -> Arc<Mutex<BackgroundTaskSupervisor>>;

    pub async fn teardown(
        &self,
        cancel_port: &dyn CancelPort,
        workflow_control: Option<Arc<dyn WorkflowControlPort>>,
        reason: &str,
    ) -> BackgroundInflightReport;
}
```

Rules:

- `BackgroundSupervisorHandle` owns `CommandCompletionHeartbeat`.
- `BackgroundSupervisorHandle::notifications()` returns the exact
  `NotificationService` clone that `run_agent` must pass into `AgentRunInput`.
- The heartbeat writes into the same `NotificationService` that the agent query
  loop drains.
- `owner_agent_run_id` is stored once on the runtime and must not be duplicated
  on every background record.
- `teardown` replaces `cancel_for_parent_exit`.

### 8.3 CommandCompletionHeartbeat

The heartbeat is an RAII runner owned by `BackgroundSupervisorHandle`.

```rust
pub(super) struct CommandCompletionHeartbeat {
    join: JoinHandle<()>,
}
```

Methods:

```rust
impl CommandCompletionHeartbeat {
    pub(super) fn spawn(
        owner_agent_run_id: AgentRunId,
        inner: Arc<Mutex<BackgroundTaskSupervisor>>,
        notifications: NotificationService,
        transport: Arc<dyn SandboxTransport>,
        interval: Duration,
    ) -> Self;
}

impl Drop for CommandCompletionHeartbeat {
    fn drop(&mut self) {
        self.join.abort();
    }
}
```

Reference-cycle rule:

- The heartbeat task must not capture `Arc<BackgroundSupervisorRuntime>`.
- It may capture only:
  - `owner_agent_run_id`,
  - `inner`,
  - `notifications`,
  - `transport`,
  - `interval`.

Idle behavior:

- The heartbeat wakes every configured interval.
- It locks the local `BackgroundTaskSupervisor`.
- It asks for running command sessions grouped by sandbox.
- If no command sessions are running, it makes no sandbox RPC.
- It sleeps and repeats until the owning `BackgroundSupervisorHandle` runtime is
  dropped, at which point `Drop` aborts the task.

### 8.4 BackgroundTaskSupervisor

The per-agent ledger. It is not a request-global map.

```rust
#[derive(Debug, Default)]
pub struct BackgroundTaskSupervisor {
    subagents: SubagentLane,
    workflows: WorkflowLane,
    commands: CommandSessionLane,
}
```

Methods:

```rust
impl BackgroundTaskSupervisor {
    pub fn new() -> Self;
    pub fn inflight_report(&self) -> BackgroundInflightReport;
    pub fn drain_command_session_notifications(&mut self) -> Vec<SystemNotification>;

    pub fn running_command_session_ids_by_sandbox(
        &self,
    ) -> Vec<(SandboxId, Vec<CommandSessionId>)>;
}
```

Rules:

- No `agent_run_id` field on records.
- No `agent_run_id` filter parameter on per-agent ledger methods.
- `owner_agent_run_id` from `BackgroundSupervisorRuntime` is used when making
  sandbox completion-collection calls.

## 9. Lane Classes and Fields

### 9.1 SubagentLane

Subagents are created by agent-core and run as local Tokio tasks, so this lane
owns local identity generation and a local abort backstop.

```rust
#[derive(Debug, Default)]
pub struct SubagentLane {
    next_session_seq: u64,
    records: HashMap<SubagentSessionId, SubagentRecord>,
}

#[derive(Debug, Clone)]
pub struct SubagentHandle {
    pub subagent_session_id: SubagentSessionId,
    pub child_agent_run_id: AgentRunId,
    pub driver_abort: AbortHandle,
}

#[derive(Debug, Clone)]
pub struct SubagentRecord {
    pub handle: SubagentHandle,
    pub tool_input: JsonObject,
    pub status: BackgroundTaskStatus,
    pub result: Option<ToolResult>,
}
```

Rules:

- `next_session_seq` exists only here because agent-core mints
  `subagent_session_id`.
- The child agent run itself gets its own `AgentRunControl` and
  `BackgroundSupervisorHandle`.
- Cancellation calls `cancel_agent_run(child_agent_run_id)` first and uses
  `driver_abort` only as a runaway-driver backstop.

### 9.2 WorkflowLane

Workflows are created and cancelled through workflow control. The supervisor
stores the public workflow handle and status, not a local Tokio abort handle.

```rust
#[derive(Debug, Default)]
pub struct WorkflowLane {
    records: HashMap<WorkflowSessionId, WorkflowBackgroundRecord>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkflowHandle {
    pub workflow_task_id: WorkflowSessionId,
    pub workflow_id: WorkflowId,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkflowBackgroundRecord {
    pub handle: WorkflowHandle,
    pub status: BackgroundTaskStatus,
}
```

Rules:

- Cancellation dispatches through `cancel_workflow`.
- The workflow's durable lifecycle remains owned by `eos-workflow`.
- The supervisor record owns in-flight accounting and parent-exit cleanup state.

### 9.3 CommandSessionLane

Command sessions are created by sandbox/eos-command-session. The supervisor
stores the public command-session handle, sandbox routing facts, status, and
completion result latch.

```rust
#[derive(Debug, Default)]
pub struct CommandSessionLane {
    records: HashMap<CommandSessionId, CommandSessionRecord>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandSessionHandle {
    pub command_session_id: CommandSessionId,
    pub sandbox_id: SandboxId,
}

#[derive(Debug, Clone)]
pub struct CommandSessionRecord {
    pub handle: CommandSessionHandle,
    pub command: String,
    pub status: BackgroundTaskStatus,
    pub result: Option<Value>,
}
```

Rules:

- `exec_command` registers a background command-session record when the daemon
  returns `status=running` and a `command_session_id`.
- This means the command did not finish within `yield_time_ms`; it is not an
  `exec_command` failure.
- The foreground daemon invocation is still cancelable before the response
  returns through `CommandInvocationHandle`.
- Completion collection dispatches through sandbox RPC
  `api.v1.command.collect_completed`.
- The heartbeat uses `owner_agent_run_id.as_str()` as the sandbox `caller_id`
  for collection.

## 10. Port Signatures

The tool-facing ports should no longer expose `agent_run_id` filters. The handle
already scopes all behavior to one agent run.

### 10.1 BackgroundSupervisorPort

```rust
#[async_trait]
pub trait BackgroundSupervisorPort: Sealed + Send + Sync {
    async fn spawn(
        &self,
        ctx: &ExecutionMetadata,
        agent_name: &str,
        prompt: &str,
    ) -> Result<SpawnedSubagent, ToolError>;

    async fn progress(
        &self,
        subagent_session_id: &SubagentSessionId,
        last_n_messages: u8,
    ) -> Result<ToolResult, ToolError>;

    async fn cancel(
        &self,
        subagent_session_id: &SubagentSessionId,
        reason: &str,
    ) -> Result<ToolResult, ToolError>;

    async fn inflight_report(&self) -> BackgroundInflightReport;

    async fn register_workflow(&self, workflow: &StartedWorkflowHandle);

    async fn cancel_workflow_record(
        &self,
        workflow_task_id: &WorkflowSessionId,
        reason: &str,
    ) -> bool;
}
```

Removed from the final port:

```rust
async fn inflight_report(&self, agent_run_id: Option<&AgentRunId>);
async fn cancel_subagents_for_agent_run(&self, agent_run_id: &AgentRunId);
async fn cancel_for_parent_exit(
    &self,
    agent_run_id: Option<&AgentRunId>,
    workflow_control: Option<Arc<dyn WorkflowControlPort>>,
    reason: &str,
);
```

### 10.2 CommandSessionSupervisorPort

```rust
#[async_trait]
pub trait CommandSessionSupervisorPort: Sealed + Send + Sync {
    async fn register(
        &self,
        command_session_id: &CommandSessionId,
        sandbox_id: &SandboxId,
        command: &str,
    );

    async fn command_session_result(
        &self,
        command_session_id: &CommandSessionId,
    ) -> Option<Value>;

    async fn mark_command_session_reported(
        &self,
        command_session_id: &CommandSessionId,
        result: Value,
    );

    async fn command_session_already_reported(
        &self,
        command_session_id: &CommandSessionId,
    ) -> bool;
}
```

Migration note:

- A staged implementation may temporarily keep the old `agent_run_id` arguments
  and assert they match `owner_agent_run_id`.
- The final API removes them.

## 11. Runtime Wiring

### 11.1 Root Agent

`eos-runtime/src/entry.rs` creates request-level immutable factories after
sandbox provisioning and before root/workflow composition:

```rust
let background_factory = Arc::new(BackgroundSupervisorFactory::new(
    services.engine_run_handles(&workspace_root),
    services.sandbox.transport.clone(),
    services.engine.command_session_completion_poll_interval(),
));
```

When the root `AgentRunId` is minted:

```rust
let background = background_factory.create(agent_run_id.clone());
let control = AgentRunControl::new(
    agent_run_id.clone(),
    Some(root_task_id.clone()),
    background,
    AgentRunFinalization::persisted(),
);
agent_run_registry.insert(control.clone());

run_agent(
    &services.engine_run_handles(&workspace_root),
    AgentRunInput {
        agent_run_id,
        background_supervisor: Some(control.background()),
        command_session_supervisor: Some(control.background()),
        notifier: control.notifications(),
        stop: control.stop(),
        foreground: control.foreground(),
        // other fields unchanged
    },
    on_event.as_ref(),
).await;
```

Rules:

- No request-level `NotificationService`.
- No request-level heartbeat.
- No request-level `cancel_for_parent_exit(None, ...)` sweep.
- Root natural finalization remains inside `run_agent` and the unfinished-root
  CAS guard.
- Hard cancellation goes through `cancel_agent_core_user_request`.

### 11.2 Workflow Agents

`RuntimeAgentRunner` stores the factory and cancellation registry, not mutable
per-agent objects.

```rust
pub(crate) struct RuntimeAgentRunner {
    services: RuntimeServices,
    workspace_root: String,
    attempt_submission: Arc<dyn AttemptSubmissionPort>,
    workflow_control: Arc<OnceLock<Arc<dyn WorkflowControlPort>>>,
    background_factory: Arc<BackgroundSupervisorFactory>,
    agent_run_registry: AgentRunRegistry,
}
```

Removed fields:

```rust
background_supervisor: Arc<dyn BackgroundSupervisorPort>,
command_session_supervisor: Arc<dyn CommandSessionSupervisorPort>,
notifier: NotificationService,
```

Inside each `run()`:

```rust
let agent_run_id = AgentRunId::new_v4();
let background = self.background_factory.create(agent_run_id.clone());
let control = AgentRunControl::new(
    agent_run_id.clone(),
    Some(launch.task_id().clone()),
    background,
    AgentRunFinalization::persisted(),
);
self.agent_run_registry.insert(control.clone());

let run = run_agent(
    &self.services.engine_run_handles(&self.workspace_root),
    AgentRunInput {
        agent_run_id,
        background_supervisor: Some(control.background()),
        command_session_supervisor: Some(control.background()),
        notifier: control.notifications(),
        stop: control.stop(),
        foreground: control.foreground(),
        // other fields unchanged
    },
    None,
).await;
```

Rules:

- Every planner/generator/reducer run gets a fresh `AgentRunControl`,
  supervisor, notifier, and heartbeat.
- Shared workflow state remains in workflow stores and the `WorkflowControlPort`.

### 11.3 Subagent Runs

Subagents follow the same per-agent runtime rule.

When `BackgroundSupervisorPort::spawn` launches a child run:

```rust
let child_agent_run_id = AgentRunId::new_v4();
let child_background = BackgroundSupervisorHandle::new(
    child_agent_run_id.clone(),
    self.runtime.handles.clone(),
    self.runtime.transport.clone(),
    self.runtime.completion_poll_interval,
);
let child_control = AgentRunControl::new(
    child_agent_run_id.clone(),
    None,
    child_background,
    AgentRunFinalization::ephemeral(),
);
agent_run_registry.insert(child_control.clone());

let run_input = AgentRunInput {
    agent_run_id: child_agent_run_id.clone(),
    background_supervisor: Some(child_control.background()),
    command_session_supervisor: Some(child_control.background()),
    notifier: child_control.notifications(),
    stop: child_control.stop(),
    foreground: child_control.foreground(),
    // subagent-specific fields unchanged
};
```

Policy choice:

- Preferred: subagents can own command sessions because their heartbeat drains to
  their own notification manager.
- If subagents must remain foreground-only for command sessions, keep
  `command_session_supervisor: None`, but document it as explicit product policy,
  not a workaround for request-level notification ownership.

## 12. Agent-Core Cancellation Flow

### 12.1 Request-Level Entry

`eos-runtime` exposes the agent-core cancellation entry point.

```rust
pub async fn cancel_agent_core_user_request(
    services: &RuntimeServices,
    request_id: &RequestId,
    reason: &str,
) -> Result<CancelReport>;
```

Flow:

```text
cancel_agent_core_user_request(request_id, reason)
  ├─ root_task_id = root_task_id_for(request_id)
  ├─ cancel_task(root_task_id, reason)
  └─ request_store.finish_request(request_id, RequestStatus::Cancelled)
```

Rules:

- This is agent-core state only.
- It does not destroy the sandbox.
- It does not call `commit_to_workspace`.
- Backend-server calls this and then calls the sandbox cancellation boundary.

### 12.2 cancel_task

```text
cancel_task(task_id, reason)
  ├─ set_task_status_if_current({Pending, Running} -> Cancelled)
  └─ if live_run = AgentRunRegistry::agent_run_for_task(task_id):
       cancel_agent_run(live_run, reason)
```

Rules:

- CAS makes repeated calls idempotent.
- `Cancelled` blocks descendants in the plan DAG the same way `Failed` does.
- If no live run exists, task-state cancellation is still complete.

### 12.3 cancel_agent_run

```text
cancel_agent_run(agent_run_id, reason)
  ├─ control = AgentRunRegistry::get(agent_run_id)
  ├─ control.stop.request(reason)
  ├─ control.foreground.teardown(cancel_port, reason)
  ├─ control.background.teardown(cancel_port, workflow_control, reason)
  ├─ finish_agent_run(Cancelled) + message_record.finish(Cancelled)
  └─ AgentRunRegistry::remove(agent_run_id)
```

Rules:

- This is awaited end-to-end.
- No cleanup path may rely on `Drop`.
- No cleanup path may spawn untracked fire-and-forget tasks.
- Idempotency is registry presence plus task/request status CAS.

### 12.4 Workflow Decomposition

`eos-workflow` owns workflow/iteration/attempt cancellation. It decomposes into
`cancel_task` and `cancel_agent_run` through `CancelPort`.

```text
cancel_workflow(workflow_task_id, reason)
  ├─ for open iteration: cancel_iteration(iteration_id, reason)
  └─ workflow_store.set_status(Cancelled)

cancel_iteration(iteration_id, reason)
  ├─ for open attempt: cancel_attempt(attempt_id, reason)
  └─ iteration_store.set_status(Cancelled)

cancel_attempt(attempt_id, reason)
  ├─ tasks = planner_task_id ∪ generator_task_ids ∪ reducer_task_ids
  ├─ latch_attempt_tasks_cancelled(tasks)
  ├─ for task_id in tasks: cancel_task(task_id, reason)
  └─ attempt_store.close(AttemptClosure::Cancelled)
```

Rules:

- Latch all attempt tasks to `Cancelled` before tearing down any live run.
- This prevents the scheduler from launching a task into the cancellation gap.
- `cancel_workflow` must not mutate the parent task.

## 13. Heartbeat and Notification Flow

### 13.1 Heartbeat

```text
CommandCompletionHeartbeat tick
  ├─ lock per-agent BackgroundTaskSupervisor
  ├─ collect running command sessions grouped by sandbox
  ├─ if empty: no sandbox RPC
  ├─ for each sandbox group:
  │    └─ api.v1.command.collect_completed(
  │         caller_id = owner_agent_run_id.as_str(),
  │         command_session_ids = ids
  │       )
  ├─ ingest returned completions into local CommandSessionLane
  ├─ render [BACKGROUND COMPLETED] notifications
  └─ enqueue into this handle's NotificationService
```

Invariant:

- The heartbeat producer and query-loop consumer share the same
  `NotificationService` instance for exactly one agent run.

### 13.2 Notifications

```text
Query loop top of turn
  ├─ evaluate per-run NotificationRule values
  ├─ enqueue rule notifications into ctx.notifier
  ├─ heartbeat may also enqueue command-completion notifications
  ├─ drain ctx.notifier
  ├─ append notifications as provider-visible user message blocks
  └─ emit StreamEvent::SystemNotification
```

Rules:

- `notification_fired` remains per `QueryContext`.
- `NotificationService` must also be per agent run.
- No workflow agent can drain another workflow agent's command completion.
- Advisor/helper runs that have no background tools may still use a standalone
  fresh `NotificationService`.

## 14. State and Store Changes

| Add / Change | Home | Notes |
| --- | --- | --- |
| `RequestStatus::Cancelled` | `eos-state/src/request.rs` | terminal request status written by `cancel_agent_core_user_request` |
| `TaskStatus::Cancelled` | `eos-state/src/task.rs` | terminal task status; blocks DAG descendants |
| `AttemptStatus::Cancelled` | `eos-state/src/attempt.rs` | terminal attempt status |
| `AttemptClosure::Cancelled { reason, outcomes, closed_at }` | `eos-state/src/attempt.rs` | close payload |
| `TaskStore::latch_attempt_tasks_cancelled(attempt_id, ids)` | `eos-state` + `eos-db` | bulk CAS before teardown |
| request/task/attempt exhaustive matches | `eos-db`, `eos-workflow`, `eos-runtime` | update status conversions and terminal checks |

Cancelled task terminal payload:

```json
{
  "fail_reason": "cancelled",
  "reason": "<reason>"
}
```

## 15. Current-to-Target Changes

| Current Item | Target |
| --- | --- |
| request-scoped `BackgroundSupervisorHandle` in `entry.rs` | per-agent `AgentRunControl.background` |
| request-scoped `NotificationService` | per-agent `BackgroundSupervisorHandle.notifications` |
| request-scoped heartbeat | per-agent `CommandCompletionHeartbeat` owned by the handle |
| `BackgroundRunFinalizer` normal cleanup | explicit awaited `AgentRunControl::teardown` |
| `BackgroundSupervisorPort::cancel_for_parent_exit` | internal concrete `BackgroundSupervisorHandle::teardown` |
| `inflight_report(Option<&AgentRunId>)` | per-agent no-arg `inflight_report()` |
| record-level `agent_run_id` fields | `owner_agent_run_id` on `BackgroundSupervisorRuntime` only |
| `SubagentRecord` side-map abort handle | `SubagentRecord { handle: SubagentHandle, ... }` |
| `WorkflowBackgroundRecord { workflow_task_id, agent_run_id }` | `WorkflowBackgroundRecord { handle: WorkflowHandle, status }` |
| `CommandSessionRecord { command_session_id, sandbox_id, agent_run_id }` | `CommandSessionRecord { handle: CommandSessionHandle, command, status, result }` |
| backend future drop as cancel | backend calls `cancel_agent_core_user_request`; see backend spec |
| workflow-specific shallow cancel helpers | `cancel_workflow -> cancel_iteration -> cancel_attempt -> cancel_task` |
| command session cancelable only after yielded background response | foreground `CommandInvocationHandle` plus background `CommandSessionHandle` |

## 16. Implementation Phases

### Phase 1: State Variants

- Add `Cancelled` variants and exhaustive-match updates.
- Add cancelled task terminal payload.
- Add bulk task latch for attempt cancellation.

Verification:

```sh
(cd agent-core && cargo check -p eos-state -p eos-db --all-targets)
```

### Phase 2: AgentRunControl and Registry

- Add `StopSignal`.
- Add `ForegroundExecutor`.
- Add `AgentRunControl`.
- Add `AgentRunRegistry`.
- Thread `stop` and `foreground` through `AgentRunInput` / `QueryContext`.
- Poll `StopSignal` at query-loop turn boundaries.

Verification:

```sh
(cd agent-core && cargo test -p eos-engine --all-targets)
```

### Phase 3: Local Background Supervisor

- Add `BackgroundSupervisorFactory`.
- Change `BackgroundSupervisorHandle` to wrap `BackgroundSupervisorRuntime`.
- Move `NotificationService` and `CommandCompletionHeartbeat` into the runtime.
- Make root/workflow/subagent runs create local handles.
- Remove request-level supervisor/notifier/heartbeat.

Verification:

```sh
(cd agent-core && cargo test -p eos-runtime --all-targets)
```

### Phase 4: Lane Handles

- Introduce `SubagentLane`, `WorkflowLane`, and `CommandSessionLane`.
- Move every record to `handle + status + metadata`.
- Remove record-level `agent_run_id`.
- Remove optional `agent_run_id` filters from per-agent supervisor methods.

### Phase 5: CancelableResource and CancelPort

- Add `CancelableResource`.
- Add `CancelPort`.
- Implement `cancel_task`.
- Implement `cancel_agent_run`.
- Replace `BackgroundRunFinalizer` normal cleanup with explicit awaited teardown.

### Phase 6: Workflow Cancellation Decomposition

- Implement `cancel_workflow`, `cancel_iteration`, `cancel_attempt`.
- Latch attempt tasks before teardown.
- Drop shallow workflow-cancel helpers that do not decompose through tasks.

### Phase 7: Request Cancellation Entry

- Add `cancel_agent_core_user_request`.
- Backend-server will call this through its cancellation coordinator.
- No sandbox cleanup is performed here.

### Phase 8: Tests and Documentation

- Update architecture docs and tests.
- Refresh stale references to per-request supervisor/notifier/heartbeat.
- Mark `uniform_recursive_cancellation_SPEC.md` as split/superseded.

## 17. Required Tests

### Runtime Wiring

- Root agent background command completion reaches the root's own notifier.
- Workflow agent A cannot drain workflow agent B's command completion.
- `RuntimeAgentRunner` does not store a shared `NotificationService`.
- Request teardown does not call request-wide `cancel_for_parent_exit(None, ...)`.

### Heartbeat

- Heartbeat with no running command sessions performs no sandbox RPC.
- Heartbeat with a running command session calls
  `api.v1.command.collect_completed` using `owner_agent_run_id`.
- A completion is enqueued into the same notifier passed to that agent's
  `AgentRunInput`.
- Dropping the last `BackgroundSupervisorHandle` aborts the heartbeat task.

### Cancellation

- `cancel_agent_run` finishes `agent_run` and message records as cancelled.
- `cancel_task` marks running/pending tasks cancelled and no-ops on terminal
  tasks.
- `cancel_attempt` latches planner/generator/reducer task rows before teardown.
- Nested `delegate_workflow` cancellation reaches every open generator/reducer
  task.
- `ask_advisor` cancellation cancels the inline child run.
- `exec_command` is cancelable during the foreground `yield_time_ms` wait through
  `CommandInvocationHandle`.

### Lanes

- `SubagentLane` mints stable `subagent_<n>` ids and stores
  `SubagentHandle`.
- `WorkflowLane` stores `WorkflowHandle { workflow_task_id, workflow_id }`.
- `CommandSessionLane` stores
  `CommandSessionHandle { command_session_id, sandbox_id }`.
- No lane record stores `agent_run_id`.

### Cleanup

- Per-agent teardown cancels only that handle's subagents, workflows, and command
  sessions.
- Command-session cancellation uses `owner_agent_run_id` as sandbox caller id.
- Workflow cancellation dispatches through workflow cancellation decomposition.
- Calling cancel twice is a no-op.

## 18. Documentation Updates

Refresh at least these sources after implementation:

```text
agent-core/crates/eos-engine/src/background/heartbeat.rs
agent-core/crates/eos-engine/src/runtime/types.rs
agent-core/crates/eos-engine/src/query/context.rs
agent-core/crates/eos-engine/src/agent/factory.rs
agent-core/crates/eos-runtime/src/entry.rs
agent-core/crates/eos-runtime/src/agent_runner.rs
agent-core/crates/eos-runtime/tests/unit/background.rs
docs/architecture/agent_loops/background-operations.html
docs/architecture/agent_loops/notifications-messages.html
docs/architecture/agent_loops/main-loop.html
docs/architecture/rust-migration.html
docs/architecture/workflow/index.html
```

Replace stale wording:

- `per-request notification sink`
- `per-request heartbeat`
- `request-scoped supervisor`
- `cancel_for_parent_exit(None, ...)`
- comments that say subagent command sessions are disabled only because a
  request-level heartbeat would route notifications to the root.

## 19. Acceptance Criteria

- Every root/workflow/subagent run receives a fresh `AgentRunControl`.
- Every `AgentRunControl` owns a fresh `BackgroundSupervisorHandle`.
- `BackgroundSupervisorHandle` owns `NotificationService`.
- `BackgroundSupervisorHandle` owns `CommandCompletionHeartbeat`.
- The heartbeat does not capture `Arc<BackgroundSupervisorRuntime>`.
- The heartbeat makes no sandbox RPC while no command sessions are running.
- `RuntimeAgentRunner` stores no per-agent mutable notifier or supervisor.
- The background ledger is split into subagent, workflow, and command-session
  lanes.
- Every lane record contains a first-class handle object.
- No background record stores `agent_run_id`.
- Request runtime owns only shared factories and workflow composition.
- `cancel_task` and `cancel_agent_run` are the only agent-core cancellation
  primitives.
- Workflow cancellation decomposes through workflow -> iteration -> attempt ->
  task.
- Cancellation is awaited end-to-end inside agent-core.
- Tests prove command-completion notifications cannot cross agent-run queues.
