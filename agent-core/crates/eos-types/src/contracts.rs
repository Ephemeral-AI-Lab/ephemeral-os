//! Cross-crate lifecycle contracts.
//!
//! These modules hold owner-neutral behavior traits and passive DTOs shared
//! across sibling crates. Keeping them in `eos-types` avoids dependency cycles:
//! engine, workflow, tools, and agent-run can all consume the contracts without
//! depending on each other's concrete implementations.

mod agent_run {
    //! Agent-run lifecycle launch contracts.

    use async_trait::async_trait;
    use crate::{
        AgentName, AgentRunId, AgentType, AttemptId, IterationId, JsonObject, Message, RequestId,
        SandboxId, ToolUseId, WorkflowId,
    };

    /// Request to spawn any agent kind.
    #[derive(Debug, Clone)]
    pub struct SpawnAgentRequest {
        /// Agent-run id to launch.
        pub agent_run_id: AgentRunId,
        /// Agent profile name to launch.
        pub agent_name: AgentName,
        /// Expected runtime profile type to launch.
        pub agent_type: AgentType,
        /// Owning request id.
        pub request_id: RequestId,
        /// Optional parent agent-run id for lineage.
        pub parent_agent_run_id: Option<AgentRunId>,
        /// Initial transcript.
        pub initial_messages: Vec<Message>,
        /// Durable launch fact for tool-launched runs.
        pub tool_use_id: Option<ToolUseId>,
        /// Bound sandbox id.
        pub sandbox_id: Option<SandboxId>,
        /// Request-visible workspace root.
        pub workspace_root: String,
        /// Whether the caller is in isolated-workspace mode.
        pub is_isolated_workspace_mode: bool,
    }

    /// Runtime-only metadata snapshot for one agent run.
    #[derive(Debug, Clone)]
    pub struct AgentRunRuntimeSnapshot {
        /// Agent-run id.
        pub agent_run_id: AgentRunId,
        /// Bound agent profile name.
        pub agent_name: String,
        /// Owning request id.
        pub request_id: Option<RequestId>,
        /// Owning workflow id.
        pub workflow_id: Option<WorkflowId>,
        /// Owning workflow iteration id.
        pub iteration_id: Option<IterationId>,
        /// Owning attempt id.
        pub attempt_id: Option<AttemptId>,
        /// Bound sandbox id.
        pub sandbox_id: Option<SandboxId>,
        /// Request-visible workspace root.
        pub workspace_root: String,
        /// Whether the run currently has an isolated workspace open.
        pub is_isolated_workspace_mode: bool,
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
        async fn spawn_agent(
            &self,
            request: SpawnAgentRequest,
        ) -> Result<AgentRunId, AgentRunError>;

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
}
mod cancellation {
    //! Agent-core cancellation contracts.

    use async_trait::async_trait;

    use crate::{AgentRunId, CoreError};

    /// Error returned by recursive request/workflow cancellation.
    #[derive(Debug, thiserror::Error)]
    #[non_exhaustive]
    pub enum CancelError {
        /// An upstream store operation failed.
        #[error("store error: {0}")]
        Store(#[from] CoreError),
        /// A lifecycle invariant broke or an internal operation failed.
        #[error("{0}")]
        Internal(String),
    }

    /// Recursive agent-core cancellation primitives.
    #[async_trait]
    pub trait AgentCoreCancellationApi: Send + Sync {
        /// Cancel a live agent run.
        async fn cancel_agent_run(
            &self,
            agent_run_id: &AgentRunId,
            reason: &str,
        ) -> Result<(), CancelError>;
    }
}
mod record;
mod workflow;

pub use agent_run::{
    AgentRunApi, AgentRunError, AgentRunOutcome, AgentRunRuntimeSnapshot, AgentRunStatus,
    SpawnAgentRequest,
};
pub use cancellation::{AgentCoreCancellationApi, CancelError};
pub use record::{
    format_record_dir, AgentRunRecordDir, AgentRunRecordIndex, AgentRunRecordTarget,
    CreatedAgentRun, WorkflowCoordinates, WorkflowTaskRole,
};
pub use workflow::{
    OpenDelegatedWorkflow, StartWorkflowRequest, StartedWorkflow, SubmissionAck, TerminalWorkflow,
    WorkflowApi, WorkflowApiError, WorkflowAttemptSubmissionApi, WorkflowTerminalStatus,
};
