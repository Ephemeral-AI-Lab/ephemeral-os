//! Cross-crate lifecycle contracts.
//!
//! This module holds owner-neutral trait ports and passive DTOs that are shared
//! across sibling crates. Keeping them in `eos-types` avoids dependency cycles:
//! engine, workflow, tools, and agent-run can all consume the contracts without
//! depending on each other's concrete implementations.

use async_trait::async_trait;

use crate::{
    AgentName, AgentRunId, AttemptId, CoreError, IterationId, JsonObject, Message, RequestId,
    SandboxId, TaskId, WorkflowId,
};

/// Request to spawn any agent kind.
#[derive(Debug, Clone)]
pub struct SpawnAgentRequest {
    /// Agent profile name to launch.
    pub agent_name: AgentName,
    /// Optional caller-provided run id; one is minted when absent.
    pub agent_run_id: Option<AgentRunId>,
    /// Initial transcript.
    pub initial_messages: Vec<Message>,
    /// Parent agent-run id, for helper/subagent lineage.
    pub parent_agent_run_id: Option<AgentRunId>,
    /// Owning request id.
    pub request_id: Option<RequestId>,
    /// Owning task id.
    pub task_id: Option<TaskId>,
    /// Owning attempt id.
    pub attempt_id: Option<AttemptId>,
    /// Owning workflow id.
    pub workflow_id: Option<WorkflowId>,
    /// Bound sandbox id.
    pub sandbox_id: Option<SandboxId>,
    /// Request-visible workspace root.
    pub workspace_root: String,
    /// Whether the caller is in isolated-workspace mode.
    pub is_isolated_workspace_mode: bool,
    /// Whether to persist the run row.
    pub persist: bool,
    /// Message-record kind.
    pub record_kind: AgentRunMessageRecordKind,
}

/// Agent-run message-record layout choice carried by callers without exposing
/// the private message-record writer crate outside the runner.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum AgentRunMessageRecordKind {
    /// Root request agent.
    Root,
    /// Delegated workflow planner/generator/reducer task agent.
    WorkflowTask {
        /// Owning workflow id.
        workflow_id: WorkflowId,
        /// Owning iteration id.
        iteration_id: IterationId,
        /// Owning attempt id.
        attempt_id: AttemptId,
        /// Workflow task role.
        role: WorkflowTaskRole,
    },
    /// Background subagent run under a parent agent.
    Subagent {
        /// Parent agent-run id.
        parent_agent_run_id: AgentRunId,
    },
    /// Advisor run under a parent agent.
    Advisor {
        /// Parent agent-run id.
        parent_agent_run_id: AgentRunId,
    },
    /// Generic agent run when no narrower layout is known.
    Agent,
}

/// Workflow task role used for message-record path labels.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum WorkflowTaskRole {
    /// Planner task.
    Planner,
    /// Generator task.
    Generator,
    /// Reducer task.
    Reducer,
}

/// Terminal outcome for one agent run.
#[derive(Debug, Clone)]
pub struct AgentRunOutcome {
    /// Agent-run id.
    pub agent_run_id: AgentRunId,
    /// Terminal status.
    pub status: AgentRunStatus,
    /// Persisted submission payload, when available.
    pub submission_payload: Option<JsonObject>,
    /// Final message history, when the runner makes it available.
    pub message_history: Vec<Message>,
    /// Provider token count, when known.
    pub token_count: Option<i64>,
    /// Framework/engine error summary, when the run failed.
    pub error: Option<String>,
}

/// Agent-run terminal status.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentRunStatus {
    /// The run completed normally.
    Completed,
    /// The run failed.
    Failed,
    /// The run was cancelled.
    Cancelled,
}

/// Error returned by the agent-run lifecycle API.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum AgentRunError {
    /// The requested agent run is not active in this process and has no durable
    /// terminal outcome available.
    #[error("agent run {0} is not active in this process")]
    NotActiveInProcess(AgentRunId),

    /// The requested agent name was not registered.
    #[error("agent {0:?} is not registered")]
    AgentNotRegistered(String),

    /// The requested agent exists but is not launchable for this operation.
    #[error("agent {agent_name:?} is not a {expected} agent (actual: {actual})")]
    WrongAgentType {
        /// Requested agent name.
        agent_name: String,
        /// Expected type label.
        expected: &'static str,
        /// Actual type label.
        actual: &'static str,
    },

    /// Recursive subagent launch is disallowed.
    #[error("subagents may not spawn further subagents")]
    RecursiveSubagent,

    /// Waiting failed because the completion channel closed.
    #[error("agent run completion channel closed for {0}")]
    CompletionChannelClosed(AgentRunId),

    /// A store, engine, or framework operation failed.
    #[error("agent run failed: {0}")]
    Internal(String),
}

/// Lifecycle API for spawning, waiting, polling, and cancelling agent runs.
#[async_trait]
pub trait AgentRunApi: Send + Sync {
    /// Spawn an agent and return its natural run id immediately.
    async fn spawn_agent(&self, request: SpawnAgentRequest) -> Result<AgentRunId, AgentRunError>;

    /// Wait for one agent run to publish a terminal outcome.
    async fn wait_for_agent_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<AgentRunOutcome, AgentRunError>;

    /// Nonblocking terminal outcome poll for background managers.
    async fn poll_agent_run_outcome(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunOutcome>, AgentRunError>;

    /// Cancel one active agent run.
    async fn cancel_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        reason: &str,
    ) -> Result<(), AgentRunError>;
}

/// Request to start a delegated workflow.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartWorkflowRequest {
    /// Parent task launching the workflow.
    pub parent_task_id: TaskId,
    /// Agent run that owns the launch.
    pub agent_run_id: AgentRunId,
    /// Delegated workflow goal.
    pub workflow_goal: String,
}

/// A started delegated workflow, keyed by its natural [`WorkflowId`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartedWorkflow {
    /// The persisted workflow id.
    pub workflow_id: WorkflowId,
    /// The delegated goal, retained for background-session display.
    pub workflow_goal: String,
}

/// Terminal status for a delegated workflow.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkflowTerminalStatus {
    /// The workflow succeeded.
    Completed,
    /// The workflow failed.
    Failed,
    /// The workflow was cancelled.
    Cancelled,
}

/// Terminal workflow facts for background accounting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TerminalWorkflow {
    /// The persisted workflow id.
    pub workflow_id: WorkflowId,
    /// Terminal status.
    pub status: WorkflowTerminalStatus,
}

/// One outstanding workflow launched by a parent task.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutstandingWorkflow {
    /// The persisted workflow id.
    pub workflow_id: WorkflowId,
    /// The workflow goal.
    pub workflow_goal: String,
}

/// Error returned by the delegated-workflow API. Tool callers map this onto
/// their own framework-fault enum at the tool boundary.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum WorkflowApiError {
    /// An upstream store operation failed.
    #[error("store error: {0}")]
    Store(#[from] CoreError),
    /// A lifecycle invariant broke or an internal operation failed.
    #[error("{0}")]
    Internal(String),
}

/// Delegated-workflow lifecycle operations used by the model-facing workflow
/// tools and the engine background workflow manager.
#[async_trait]
pub trait WorkflowApi: Send + Sync {
    /// Start a delegated workflow.
    async fn start_workflow(
        &self,
        request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, WorkflowApiError>;

    /// Render workflow status for the model-facing check tool.
    async fn check_workflow_status(
        &self,
        workflow_id: &WorkflowId,
    ) -> Result<String, WorkflowApiError>;

    /// Cancel a workflow by its natural id, returning a model-facing message.
    async fn cancel_workflow(
        &self,
        workflow_id: &WorkflowId,
        reason: &str,
    ) -> Result<String, WorkflowApiError>;

    /// Poll terminal workflow state for background accounting.
    async fn poll_terminal_workflow(
        &self,
        workflow_id: &WorkflowId,
    ) -> Result<Option<TerminalWorkflow>, WorkflowApiError>;

    /// All workflows this parent task still has outstanding for `agent_run_id`.
    async fn find_outstanding_workflows(
        &self,
        parent_task_id: &TaskId,
        agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, WorkflowApiError>;

    /// The delegation-ancestry depth of `workflow_id` (1 = top-level).
    async fn workflow_depth(&self, workflow_id: &WorkflowId) -> Result<u32, WorkflowApiError>;
}
