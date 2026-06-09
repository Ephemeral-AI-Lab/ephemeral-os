//! Workflow and terminal-submission contracts.

use std::collections::BTreeMap;

use async_trait::async_trait;

use crate::{
    AgentRunId, AttemptId, CoreError, GeneratorSubmission, PlanDisposition, PlanNodeId,
    ReducerSubmission, TaskId, ToolUseId, WorkflowId,
};

/// One planner-authored generator task.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanTask {
    /// Caller-assigned task id.
    pub id: PlanNodeId,
    /// Bound subagent profile name.
    pub agent_name: String,
    /// Ids this task depends on.
    pub needs: Vec<PlanNodeId>,
}

/// One planner-authored reducer task.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanReducer {
    /// Caller-assigned reducer id.
    pub id: PlanNodeId,
    /// Ids this reducer depends on.
    pub needs: Vec<PlanNodeId>,
    /// The reducer's instruction prompt.
    pub prompt: String,
}

/// A validated planner DAG submission.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlannerPlan {
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// The planner's own task.
    pub planner_task_id: TaskId,
    /// Whether the plan completes the attempt or defers a goal.
    pub disposition: PlanDisposition,
    /// The generator tasks, in submission order.
    pub tasks: Vec<PlanTask>,
    /// Per-task instruction specs, keyed by task id.
    pub task_specs: BTreeMap<PlanNodeId, String>,
    /// The reducer tasks, in submission order.
    pub reducers: Vec<PlanReducer>,
}

/// The result of applying a terminal submission.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubmissionAck {
    /// The submission was accepted by the orchestrator.
    Accepted,
    /// The submission was rejected with a model-facing message.
    Rejected(String),
}

/// Per-attempt submission application for terminal tools.
#[async_trait]
pub trait WorkflowAttemptSubmissionApi: Send + Sync {
    /// Apply a validated planner DAG.
    async fn apply_plan(&self, plan: PlannerPlan) -> Result<SubmissionAck, CoreError>;

    /// Record one generator task's terminal outcome.
    async fn submit_generator(
        &self,
        submission: GeneratorSubmission,
    ) -> Result<SubmissionAck, CoreError>;

    /// Record one reducer task's terminal outcome.
    async fn apply_reducer(
        &self,
        submission: ReducerSubmission,
    ) -> Result<SubmissionAck, CoreError>;
}

/// Request to start a delegated workflow.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartWorkflowRequest {
    /// Parent task launching the workflow.
    pub parent_task_id: TaskId,
    /// Agent run that owns the launch.
    pub agent_run_id: AgentRunId,
    /// Tool use that requested the workflow, if available.
    pub tool_use_id: Option<ToolUseId>,
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

    /// All workflows this launching agent run still has outstanding.
    async fn find_outstanding_workflows(
        &self,
        agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, WorkflowApiError>;

    /// The delegation-ancestry depth of `workflow_id` (1 = top-level).
    async fn workflow_depth(&self, workflow_id: &WorkflowId) -> Result<u32, WorkflowApiError>;
}
