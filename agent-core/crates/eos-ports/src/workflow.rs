use async_trait::async_trait;
use eos_types::{AgentRunId, TaskId, WorkflowId, WorkflowSessionId};

use crate::{Sealed, ToolError};

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
    pub status: crate::agent_run::SubagentSessionStatus,
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

/// A started delegated workflow session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartedWorkflowSession {
    /// The persisted workflow id.
    pub workflow_id: WorkflowId,
    /// The agent-facing background session id.
    pub workflow_task_id: WorkflowSessionId,
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

/// Per-attempt workflow control for delegate/check/cancel workflow tools.
///
/// This is the compatibility port moved out of `eos-tools`. Later phases replace
/// rendered `status`/`cancel` methods with resource-service methods.
#[async_trait]
pub trait WorkflowControlPort: Sealed + Send + Sync {
    /// Launch a delegated workflow from a running parent task.
    async fn start(
        &self,
        parent_task_id: &TaskId,
        agent_run_id: &AgentRunId,
        workflow_goal: &str,
    ) -> Result<StartedWorkflowSession, ToolError>;

    /// Render delegated-workflow progress.
    async fn status(
        &self,
        workflow_id: &WorkflowId,
        workflow_task_id: Option<&WorkflowSessionId>,
    ) -> Result<String, ToolError>;

    /// Cancel an outstanding delegated workflow by its background session id.
    async fn cancel(
        &self,
        workflow_task_id: &WorkflowSessionId,
        reason: &str,
    ) -> Result<String, ToolError>;

    /// All workflows this parent task still has outstanding for `agent_run_id`.
    async fn find_outstanding(
        &self,
        parent_task_id: &TaskId,
        agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, ToolError>;

    /// The delegation-ancestry depth of `workflow_id`.
    async fn workflow_depth(&self, workflow_id: &WorkflowId) -> Result<u32, ToolError>;
}

/// Workflow background-session registry for one owning agent run.
#[async_trait]
pub trait WorkflowSessionPort: Sealed + Send + Sync {
    /// Register a started workflow as background work.
    async fn register_background_session(&self, workflow: &StartedWorkflow);

    /// Count running workflow sessions for this run.
    async fn count_background_sessions(&self) -> usize;

    /// Cancel all running workflow sessions for this run.
    async fn cancel_all_background_sessions(&self, reason: &str);

    /// Poll terminal workflows and push notifications.
    async fn poll_complete_background_sessions(&self) -> usize;
}

#[async_trait]
impl<T> WorkflowServicePort for T
where
    T: WorkflowControlPort + ?Sized,
{
    async fn start_workflow(
        &self,
        request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, ToolError> {
        let started = self
            .start(
                &request.parent_task_id,
                &request.agent_run_id,
                &request.workflow_goal,
            )
            .await?;
        Ok(StartedWorkflow {
            workflow_id: started.workflow_id,
            workflow_task_id: started.workflow_task_id,
        })
    }

    async fn check_workflow_status(
        &self,
        workflow_id: &WorkflowId,
        workflow_task_id: Option<&WorkflowSessionId>,
    ) -> Result<String, ToolError> {
        self.status(workflow_id, workflow_task_id).await
    }

    async fn cancel_workflow_session(
        &self,
        workflow_task_id: &WorkflowSessionId,
        reason: &str,
    ) -> Result<String, ToolError> {
        self.cancel(workflow_task_id, reason).await
    }

    async fn poll_terminal_workflow(
        &self,
        workflow_id: &WorkflowId,
        workflow_task_id: &WorkflowSessionId,
    ) -> Result<Option<TerminalWorkflow>, ToolError> {
        let status_text = self.status(workflow_id, Some(workflow_task_id)).await?;
        let Some(status) = terminal_status(&status_text) else {
            return Ok(None);
        };
        Ok(Some(TerminalWorkflow {
            workflow_id: workflow_id.clone(),
            workflow_task_id: workflow_task_id.clone(),
            status,
        }))
    }

    async fn find_outstanding_workflows(
        &self,
        parent_task_id: &TaskId,
        agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, ToolError> {
        self.find_outstanding(parent_task_id, agent_run_id).await
    }

    async fn workflow_depth(&self, workflow_id: &WorkflowId) -> Result<u32, ToolError> {
        WorkflowControlPort::workflow_depth(self, workflow_id).await
    }
}

fn terminal_status(status_text: &str) -> Option<crate::agent_run::SubagentSessionStatus> {
    if status_text.contains("is Succeeded.") {
        Some(crate::agent_run::SubagentSessionStatus::Completed)
    } else if status_text.contains("is Failed.") {
        Some(crate::agent_run::SubagentSessionStatus::Failed)
    } else if status_text.contains("is Cancelled.") {
        Some(crate::agent_run::SubagentSessionStatus::Cancelled)
    } else {
        None
    }
}
