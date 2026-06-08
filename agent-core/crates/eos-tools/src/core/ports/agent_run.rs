use async_trait::async_trait;
use eos_agent_def::AgentName;
use eos_agent_message_records::AgentRunRecordKind;
use eos_llm_client::Message;
use eos_types::{
    AgentRunId, AttemptId, RequestId, SandboxId, SubagentSessionId, TaskId, WorkflowId,
};
use serde::Serialize;

use crate::core::{Sealed, ToolError, ToolResult};

/// Tool-owned request to spawn an agent run.
#[derive(Debug, Clone)]
pub struct SpawnAgentRequest {
    /// Agent profile name to launch.
    pub agent_name: AgentName,
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
    pub record_kind: AgentRunRecordKind,
}

/// Agent spawn failure surfaced to model-facing tools.
#[derive(Debug)]
pub enum AgentSpawnError {
    /// Validation rejected the dispatch.
    Rejected(SubagentLaunchRejection),
    /// Framework/tool error while spawning.
    Tool(ToolError),
}

/// Resource service for spawning agent runs.
#[async_trait]
pub trait AgentRunServicePort: Sealed + Send + Sync {
    /// Spawn an agent run, returning its natural run id.
    async fn spawn_agent(&self, request: SpawnAgentRequest) -> Result<AgentRunId, AgentSpawnError>;

    /// Wait for a spawned agent run and return its terminal model-facing result.
    async fn wait_for_agent_result(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<ToolResult, ToolError>;
}

/// Typed launch rejection facts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubagentLaunchRejection {
    /// The caller is already a subagent.
    Recursive,
    /// The requested agent name is not registered.
    NotRegistered {
        /// Requested agent name.
        agent_name: String,
    },
    /// The requested agent exists but is not subagent-typed.
    NotSubagent {
        /// Requested agent name.
        agent_name: String,
        /// Registered agent type string.
        agent_type: String,
    },
}

/// Terminal background status facts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubagentSessionStatus {
    /// The subagent is still running.
    Running,
    /// The subagent called its terminal tool.
    Completed,
    /// The subagent crashed or exited without terminal output.
    Failed,
    /// The subagent was cancelled.
    Cancelled,
    /// The subagent result was already delivered.
    Delivered,
}

/// Per-kind in-flight background-session count for one agent run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct BackgroundSessionCounts {
    /// `subagents + workflows + command_sessions`.
    pub total: usize,
    /// In-flight subagent runs for this agent run.
    pub subagents: usize,
    /// Outstanding delegated workflows for this agent run.
    pub workflows: usize,
    /// In-flight background-tracked command sessions for this agent run.
    pub command_sessions: usize,
}

/// Subagent background-session registry for one owning agent run.
#[async_trait]
pub trait SubagentSessionPort: Sealed + Send + Sync {
    /// Register a started child agent run as a background session.
    async fn register_background_session(
        &self,
        agent_run_id: &AgentRunId,
        agent_name: &str,
    ) -> SubagentSessionId;

    /// Cancel one tracked subagent by its natural child agent-run id.
    async fn cancel_background_agent_run(&self, agent_run_id: &AgentRunId, reason: &str) -> bool;

    /// Count running background sessions for this run.
    async fn count_background_sessions(&self) -> usize;

    /// Cancel all running background sessions for this run.
    async fn cancel_all_background_sessions(&self, reason: &str);

    /// Poll terminal child runs and push notifications.
    async fn poll_complete_background_sessions(&self) -> usize;
}
