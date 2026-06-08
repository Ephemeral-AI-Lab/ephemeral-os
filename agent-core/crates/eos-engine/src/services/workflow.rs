use std::sync::Arc;

use async_trait::async_trait;
use eos_ports::{
    OutstandingWorkflow, Sealed, StartWorkflowRequest, StartedWorkflow, SubagentSessionStatus,
    TerminalWorkflow, ToolError, WorkflowControlPort, WorkflowServicePort,
};
use eos_types::{AgentRunId, TaskId, WorkflowId, WorkflowSessionId};

#[derive(Clone)]
pub struct WorkflowService {
    control: Arc<dyn WorkflowControlPort>,
}

impl std::fmt::Debug for WorkflowService {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkflowService").finish_non_exhaustive()
    }
}

impl WorkflowService {
    #[must_use]
    pub fn new(control: Arc<dyn WorkflowControlPort>) -> Self {
        Self { control }
    }
}

impl Sealed for WorkflowService {}

#[async_trait]
impl WorkflowServicePort for WorkflowService {
    async fn start_workflow(
        &self,
        request: StartWorkflowRequest,
    ) -> Result<StartedWorkflow, ToolError> {
        let started = self
            .control
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
        self.control.status(workflow_id, workflow_task_id).await
    }

    async fn cancel_workflow_session(
        &self,
        workflow_task_id: &WorkflowSessionId,
        reason: &str,
    ) -> Result<String, ToolError> {
        self.control.cancel(workflow_task_id, reason).await
    }

    async fn poll_terminal_workflow(
        &self,
        workflow_id: &WorkflowId,
        workflow_task_id: &WorkflowSessionId,
    ) -> Result<Option<TerminalWorkflow>, ToolError> {
        let status_text = self
            .control
            .status(workflow_id, Some(workflow_task_id))
            .await?;
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
        self.control
            .find_outstanding(parent_task_id, agent_run_id)
            .await
    }

    async fn workflow_depth(&self, workflow_id: &WorkflowId) -> Result<u32, ToolError> {
        self.control.workflow_depth(workflow_id).await
    }
}

fn terminal_status(status_text: &str) -> Option<SubagentSessionStatus> {
    if status_text.contains("is Succeeded.") {
        Some(SubagentSessionStatus::Completed)
    } else if status_text.contains("is Failed.") {
        Some(SubagentSessionStatus::Failed)
    } else if status_text.contains("is Cancelled.") {
        Some(SubagentSessionStatus::Cancelled)
    } else {
        None
    }
}
