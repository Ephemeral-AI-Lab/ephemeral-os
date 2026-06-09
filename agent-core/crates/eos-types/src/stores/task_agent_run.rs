//! Task-agent-run lineage persistence contract.

use async_trait::async_trait;

use crate::{
    AgentName, AgentRunId, AgentRunRecordIndex, CoreError, CreatedTaskAgentRun, JsonObject,
    ParentAgentRunAnchor, ParentedAgentRunKind, ParentedRun, RequestId, TaskExecutionIndex, TaskId,
    TaskRun, TaskStatus, ToolUseId, WorkflowCoordinates, WorkflowTaskRole,
};

use super::Sealed;

/// Persistence surface for the merged `task_runs` and `parented_runs` lineage.
#[async_trait]
pub trait TaskAgentRunStore: Sealed + Send + Sync {
    /// Create the root task-agent-run row and bind `Request.root_task_id`.
    async fn create_root_task_agent_run(
        &self,
        request_id: &RequestId,
        task_id: &TaskId,
        agent_run_id: &AgentRunId,
        agent_name: &AgentName,
    ) -> Result<CreatedTaskAgentRun, CoreError>;

    /// Create a workflow task-agent-run row.
    async fn create_workflow_task_agent_run(
        &self,
        request_id: &RequestId,
        task_id: &TaskId,
        agent_run_id: &AgentRunId,
        workflow: &WorkflowCoordinates,
        role: WorkflowTaskRole,
        agent_name: &AgentName,
    ) -> Result<CreatedTaskAgentRun, CoreError>;

    /// Create a parent-launched subagent/advisor row with a derived own task id.
    async fn create_parented_task_agent_run(
        &self,
        agent_run_id: &AgentRunId,
        parent: &ParentAgentRunAnchor,
        kind: ParentedAgentRunKind,
        tool_use_id: Option<&ToolUseId>,
        agent_name: &AgentName,
    ) -> Result<CreatedTaskAgentRun, CoreError>;

    /// Finish a root/workflow task-agent-run row.
    async fn finish_task_run(
        &self,
        agent_run_id: &AgentRunId,
        status: TaskStatus,
        terminal_payload: Option<&JsonObject>,
        token_count: i64,
        error: Option<&str>,
    ) -> Result<Option<TaskRun>, CoreError>;

    /// Finish a parent-launched subagent/advisor row.
    async fn finish_parented_run(
        &self,
        agent_run_id: &AgentRunId,
        status: TaskStatus,
        terminal_payload: Option<&JsonObject>,
        token_count: i64,
        error: Option<&str>,
    ) -> Result<Option<ParentedRun>, CoreError>;

    /// Resolve the record-index input for one run id.
    async fn record_index_for_agent_run(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Option<AgentRunRecordIndex>, CoreError>;

    /// Load one task-agent-run row by task id.
    async fn get_task_run(&self, task_id: &TaskId) -> Result<Option<TaskRun>, CoreError>;

    /// Load parent-launched child runs for one parent task and kind.
    async fn list_parented_runs_for_parent_task(
        &self,
        parent_task_id: &TaskId,
        kind: ParentedAgentRunKind,
    ) -> Result<Vec<ParentedRun>, CoreError>;

    /// Derive the flat read-side child index for one task.
    async fn task_execution_index(
        &self,
        task_id: &TaskId,
    ) -> Result<Option<TaskExecutionIndex>, CoreError>;
}

/// Build the deterministic parented-run task id from launch facts.
///
/// # Errors
/// Returns [`CoreError`] when `tool_use_id` is absent or the derived id is not a
/// valid [`TaskId`].
pub fn parented_task_id(
    parent_agent_run_id: &AgentRunId,
    kind: ParentedAgentRunKind,
    tool_use_id: Option<&ToolUseId>,
) -> Result<TaskId, CoreError> {
    let tool_use_id = tool_use_id.ok_or_else(|| {
        CoreError::Store("parented task-agent-run creation requires tool_use_id".to_owned())
    })?;
    let segment = match kind {
        ParentedAgentRunKind::Subagent => "sub",
        ParentedAgentRunKind::Advisor => "adv",
    };
    format!(
        "{}:{segment}:{}",
        parent_agent_run_id.as_str(),
        tool_use_id.as_str()
    )
    .parse()
}
