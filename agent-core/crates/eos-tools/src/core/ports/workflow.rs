use eos_types::{AgentRunId, TaskId, WorkflowId, WorkflowSessionId};

use crate::core::{Sealed, ToolError};

use async_trait::async_trait;

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

/// A started delegated workflow.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartedWorkflow {
    /// The persisted workflow id.
    pub workflow_id: WorkflowId,
    /// The agent-facing background session id.
    pub workflow_task_id: WorkflowSessionId,
}

/// Terminal workflow facts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TerminalWorkflow {
    /// The persisted workflow id.
    pub workflow_id: WorkflowId,
    /// The agent-facing background session id.
    pub workflow_task_id: WorkflowSessionId,
    /// Terminal status for background accounting.
    pub status: super::agent_run::SubagentSessionStatus,
}

/// Resource service for workflow lifecycle operations.
#[async_trait]
pub trait WorkflowServicePort: Sealed + Send + Sync {
    /// Start a delegated workflow.
    async fn start_workflow(
        &self,
        request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, ToolError>;

    /// Render workflow status for the model-facing check tool.
    async fn check_workflow_status(
        &self,
        workflow_id: &WorkflowId,
        workflow_task_id: Option<&WorkflowSessionId>,
    ) -> Result<String, ToolError>;

    /// Cancel a workflow by the agent-facing background handle.
    async fn cancel_workflow_session(
        &self,
        workflow_task_id: &WorkflowSessionId,
        reason: &str,
    ) -> Result<String, ToolError>;

    /// Poll terminal workflow state for background accounting.
    async fn poll_terminal_workflow(
        &self,
        workflow_id: &WorkflowId,
        workflow_task_id: &WorkflowSessionId,
    ) -> Result<Option<TerminalWorkflow>, ToolError>;

    /// All workflows this parent task still has outstanding for `agent_run_id`.
    async fn find_outstanding_workflows(
        &self,
        parent_task_id: &TaskId,
        agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, ToolError>;

    /// The delegation-ancestry depth of `workflow_id`.
    async fn workflow_depth(&self, workflow_id: &WorkflowId) -> Result<u32, ToolError>;
}

/// One outstanding workflow launched by a parent task.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutstandingWorkflow {
    /// The persisted workflow id.
    pub workflow_id: WorkflowId,
    /// The agent-facing background session id.
    pub workflow_task_id: WorkflowSessionId,
    /// The workflow goal.
    pub workflow_goal: String,
}
