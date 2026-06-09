//! Agent-run lifecycle launch contracts.

use async_trait::async_trait;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::{
    AgentName, AgentRunId, AttemptId, IterationId, JsonObject, Message, PlanNodeId, RequestId,
    SandboxId, TaskId, ToolUseId, WorkflowId,
};

use super::record::{
    ParentedAgentRunKind, TaskAgentRunKind, WorkflowCoordinates, WorkflowTaskRole,
};

/// Request to spawn any agent kind.
#[derive(Debug, Clone)]
pub struct SpawnAgentRequest {
    /// Agent profile name to launch.
    pub agent_name: AgentName,
    /// Initial transcript.
    pub initial_messages: Vec<Message>,
    /// Closed spawn target and lineage facts.
    pub target: SpawnAgentTarget,
    /// Durable launch fact for workflow/parented launches. It is never part of
    /// the record-dir target.
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
    /// Owning task id.
    pub task_id: Option<TaskId>,
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

/// Parent run anchor for a parent-launched agent run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ParentAgentRunAnchor {
    /// Owning request id.
    pub request_id: RequestId,
    /// Parent task id.
    pub parent_task_id: TaskId,
    /// Parent agent-run id.
    pub agent_run_id: AgentRunId,
}

/// Closed spawn target for agent-run creation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub enum SpawnAgentTarget {
    /// Root request agent.
    Root {
        /// Owning request id.
        request_id: RequestId,
    },
    /// Workflow planner/generator/reducer task agent.
    Workflow {
        /// Owning request id.
        request_id: RequestId,
        /// Owning workflow coordinates.
        workflow: WorkflowCoordinates,
        /// Workflow task role.
        role: WorkflowTaskRole,
        /// Planner-local node id for generator/reducer launches.
        ///
        /// Planner launches do not have a planner-local node id.
        plan_node_id: Option<PlanNodeId>,
    },
    /// Parent-launched subagent run.
    Subagent {
        /// Parent run anchor.
        parent: ParentAgentRunAnchor,
    },
    /// Parent-launched advisor run.
    Advisor {
        /// Parent run anchor.
        parent: ParentAgentRunAnchor,
    },
}

impl SpawnAgentTarget {
    /// Owning request id.
    #[must_use]
    pub const fn request_id(&self) -> &RequestId {
        match self {
            Self::Root { request_id } | Self::Workflow { request_id, .. } => request_id,
            Self::Subagent { parent } | Self::Advisor { parent } => &parent.request_id,
        }
    }

    /// Workflow coordinates for workflow task-agent-runs.
    #[must_use]
    pub const fn workflow(&self) -> Option<&WorkflowCoordinates> {
        match self {
            Self::Workflow { workflow, .. } => Some(workflow),
            Self::Root { .. } | Self::Subagent { .. } | Self::Advisor { .. } => None,
        }
    }

    /// Convert the spawn target into the current task-agent-run classification.
    #[must_use]
    pub fn task_agent_run_kind(&self) -> TaskAgentRunKind {
        match self {
            Self::Root { .. } => TaskAgentRunKind::Root,
            Self::Workflow { workflow, role, .. } => TaskAgentRunKind::Workflow {
                workflow: workflow.clone(),
                role: *role,
            },
            Self::Subagent { parent } => TaskAgentRunKind::Parented {
                parent_agent_run_id: parent.agent_run_id.clone(),
                kind: ParentedAgentRunKind::Subagent,
            },
            Self::Advisor { parent } => TaskAgentRunKind::Parented {
                parent_agent_run_id: parent.agent_run_id.clone(),
                kind: ParentedAgentRunKind::Advisor,
            },
        }
    }
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
