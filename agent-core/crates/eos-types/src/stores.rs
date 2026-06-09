//! Shared async persistence stores grouped by consuming behavior boundary.

mod model_registry {
    //! Model-registry persistence contracts.

    use async_trait::async_trait;

    use crate::{CoreError, JsonObject, ModelRegistration};

    use super::Sealed;

    /// Persistence surface for [`ModelRegistration`].
    #[async_trait]
    pub trait ModelStore: Sealed + Send + Sync {
        /// Create or update a registration.
        async fn register(
            &self,
            model_key: &str,
            label: &str,
            class_path: &str,
            kwargs: &JsonObject,
            activate: bool,
        ) -> Result<ModelRegistration, CoreError>;

        /// Delete by key; `Ok(false)` means no such key.
        async fn delete(&self, model_key: &str) -> Result<bool, CoreError>;

        /// Load a registration by key.
        async fn get(&self, model_key: &str) -> Result<Option<ModelRegistration>, CoreError>;

        /// The single active registration, if any.
        async fn active(&self) -> Result<Option<ModelRegistration>, CoreError>;
    }
}
mod request_task {
    //! Runtime-facing request persistence contracts.

    use async_trait::async_trait;

    use crate::{CoreError, Request, RequestId, RequestStatus, SandboxId};

    use super::Sealed;

    /// Persistence surface for top-level requests.
    #[async_trait]
    pub trait RequestStore: Sealed + Send + Sync {
        /// Create a new request row.
        async fn create_request(
            &self,
            request_id: &RequestId,
            cwd: &str,
            sandbox_id: Option<&SandboxId>,
            request_prompt: &str,
        ) -> Result<(), CoreError>;

        /// Load a request by id.
        async fn get(&self, id: &RequestId) -> Result<Option<Request>, CoreError>;

        /// Finish the request with `status`, stamping `finished_at` server-side.
        async fn finish_request(
            &self,
            id: &RequestId,
            status: RequestStatus,
        ) -> Result<Option<Request>, CoreError>;

        /// List all requests, newest first.
        async fn list(&self) -> Result<Vec<Request>, CoreError>;
    }
}
mod task_agent_run {
    //! Agent-run lineage persistence contract.

    use async_trait::async_trait;

    use crate::{
        AgentName, AgentRun, AgentRunId, AgentRunRecordIndex, AgentType, CoreError,
        CreatedAgentRun, JsonObject, RequestId, RunningRequestAgentRun, TaskOutcome, TaskStatus,
        ToolUseId,
    };

    use super::Sealed;

    /// Persistence surface for `agent_runs` lineage.
    #[async_trait]
    pub trait AgentRunStore: Sealed + Send + Sync {
        /// Create an agent-run row for the supplied spawn target.
        async fn create_agent_run(
            &self,
            agent_run_id: &AgentRunId,
            request_id: &RequestId,
            agent_name: &AgentName,
            agent_type: AgentType,
            parent_agent_run_id: Option<&AgentRunId>,
            tool_use_id: Option<&ToolUseId>,
        ) -> Result<CreatedAgentRun, CoreError>;

        /// Finish an agent-run row.
        async fn finish_agent_run(
            &self,
            agent_run_id: &AgentRunId,
            status: TaskStatus,
            terminal_payload: Option<&JsonObject>,
            task_outcome: Option<&TaskOutcome>,
            token_count: i64,
            error: Option<&str>,
        ) -> Result<Option<AgentRun>, CoreError>;

        /// Resolve the record-index input for one run id.
        async fn record_index_for_agent_run(
            &self,
            agent_run_id: &AgentRunId,
        ) -> Result<Option<AgentRunRecordIndex>, CoreError>;

        /// Load an agent-run row by agent-run id.
        async fn get_agent_run(
            &self,
            agent_run_id: &AgentRunId,
        ) -> Result<Option<AgentRun>, CoreError>;

        /// Load agent-run rows for one request.
        async fn list_agent_runs_for_request(
            &self,
            request_id: &RequestId,
        ) -> Result<Vec<AgentRun>, CoreError>;

        /// Load running agent runs for one request.
        async fn list_running_agent_runs_for_request(
            &self,
            request_id: &RequestId,
        ) -> Result<Vec<RunningRequestAgentRun>, CoreError>;

        /// Load child agent runs for one parent agent run and kind.
        async fn list_child_agent_runs_for_parent_agent_run(
            &self,
            parent_agent_run_id: &AgentRunId,
            agent_type: Option<AgentType>,
        ) -> Result<Vec<AgentRun>, CoreError>;
    }
}
mod workflow {
    //! Workflow-facing persistence contracts.

    use async_trait::async_trait;

    use crate::{
        AgentRunId, Attempt, AttemptBudget, AttemptClosure, AttemptId, CoreError, ExecutionNode,
        Iteration, IterationCreationReason, IterationId, IterationStatus, RequestId,
        ToolUseId, UtcDateTime, WorkItemId, Workflow, WorkflowId, WorkflowStatus,
    };

    use super::Sealed;

    /// Persistence surface for [`Workflow`].
    #[async_trait]
    pub trait WorkflowStore: Sealed + Send + Sync {
        /// Insert a fresh open workflow and return it.
        async fn insert(
            &self,
            request_id: &RequestId,
            parent_agent_run_id: &AgentRunId,
            tool_use_id: Option<&ToolUseId>,
            workflow_goal: &str,
        ) -> Result<Workflow, CoreError>;

        /// Load a workflow by id.
        async fn get(&self, id: &WorkflowId) -> Result<Option<Workflow>, CoreError>;

        /// Append a child iteration id and return the updated workflow.
        async fn append_iteration_id(
            &self,
            id: &WorkflowId,
            iteration_id: &IterationId,
        ) -> Result<Workflow, CoreError>;

        /// Set status and optionally close time.
        async fn set_status(
            &self,
            id: &WorkflowId,
            status: WorkflowStatus,
            closed_at: Option<UtcDateTime>,
        ) -> Result<Workflow, CoreError>;

        /// All workflows launched by one agent run, ordered by creation.
        async fn list_for_launching_agent_run(
            &self,
            parent_agent_run_id: &AgentRunId,
        ) -> Result<Vec<Workflow>, CoreError>;

        /// Mark all open workflows for a request as cancelled.
        async fn cancel_open_workflows_for_request(
            &self,
            request_id: &RequestId,
            reason: &str,
        ) -> Result<usize, CoreError>;
    }

    /// Persistence surface for [`Iteration`].
    #[async_trait]
    pub trait IterationStore: Sealed + Send + Sync {
        /// Insert a fresh open iteration and return it.
        async fn insert(
            &self,
            workflow_id: &WorkflowId,
            sequence_no: i64,
            creation_reason: IterationCreationReason,
            workflow_goal: &str,
            iteration_goal: &str,
            attempt_budget: AttemptBudget,
        ) -> Result<Iteration, CoreError>;

        /// Load an iteration by id.
        async fn get(&self, id: &IterationId) -> Result<Option<Iteration>, CoreError>;

        /// Append a child attempt id and return the updated iteration.
        async fn append_attempt_id(
            &self,
            id: &IterationId,
            attempt_id: &AttemptId,
        ) -> Result<Iteration, CoreError>;

        /// Set status and optionally close time.
        async fn set_status(
            &self,
            id: &IterationId,
            status: IterationStatus,
            closed_at: Option<UtcDateTime>,
        ) -> Result<Iteration, CoreError>;

        /// All iterations of a workflow, ordered by sequence number.
        async fn list_for_workflow(
            &self,
            workflow_id: &WorkflowId,
        ) -> Result<Vec<Iteration>, CoreError>;

        /// Mark all open iterations for a request as cancelled.
        async fn cancel_open_iterations_for_request(
            &self,
            request_id: &RequestId,
            reason: &str,
        ) -> Result<usize, CoreError>;
    }

    /// Persistence surface for [`Attempt`].
    #[async_trait]
    pub trait AttemptStore: Sealed + Send + Sync {
        /// Insert a fresh attempt in the planning stage and return it.
        async fn insert(
            &self,
            iteration_id: &IterationId,
            workflow_id: &WorkflowId,
            attempt_sequence_no: i64,
        ) -> Result<Attempt, CoreError>;

        /// Load an attempt by id.
        async fn get(&self, id: &AttemptId) -> Result<Option<Attempt>, CoreError>;

        /// Bind the planner agent run assigned to this attempt.
        async fn bind_planner_agent_run(
            &self,
            id: &AttemptId,
            planner_agent_run_id: &AgentRunId,
        ) -> Result<Attempt, CoreError>;

        /// Record the planner-authored outcome and execution tree nodes.
        async fn record_plan_outcome(
            &self,
            id: &AttemptId,
            planner_outcome: &crate::TaskOutcome,
            nodes: &[ExecutionNode],
        ) -> Result<Attempt, CoreError>;

        /// Bind a worker agent run to one execution-tree node.
        async fn bind_worker_agent_run(
            &self,
            id: &AttemptId,
            work_item_id: &WorkItemId,
            agent_run_id: &AgentRunId,
        ) -> Result<Attempt, CoreError>;

        /// Record a worker outcome on one execution-tree node.
        async fn record_worker_outcome(
            &self,
            id: &AttemptId,
            work_item_id: &WorkItemId,
            status: crate::TaskStatus,
            outcome: &crate::TaskOutcome,
        ) -> Result<Attempt, CoreError>;

        /// Close the attempt with a typed terminal closure.
        async fn close(
            &self,
            id: &AttemptId,
            closure: AttemptClosure,
        ) -> Result<Attempt, CoreError>;

        /// All attempts of an iteration, ordered by attempt sequence number.
        async fn list_for_iteration(
            &self,
            iteration_id: &IterationId,
        ) -> Result<Vec<Attempt>, CoreError>;

        /// Mark all open attempts for a request as cancelled.
        async fn cancel_open_attempts_for_request(
            &self,
            request_id: &RequestId,
        ) -> Result<usize, CoreError>;
    }
}

pub use model_registry::ModelStore;
pub use request_task::RequestStore;
pub use task_agent_run::AgentRunStore;
pub use workflow::{AttemptStore, IterationStore, WorkflowStore};

/// Alias for the error every store method returns.
pub type StoreError = crate::CoreError;

/// Sealing marker for the store traits.
///
/// Implemented by workspace repository types and in-crate test fakes only.
#[doc(hidden)]
pub trait Sealed {}
