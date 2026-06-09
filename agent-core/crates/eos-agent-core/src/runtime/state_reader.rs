//! Narrow read-side handle over agent-core persisted state.
//!
//! [`AgentCoreRuntime::state_reader`](super::AgentCoreRuntime::state_reader) hands
//! the backend composition root the crate-owned store traits it needs to join
//! its own lifecycle rows with agent-core request/task/agent-run state — never a
//! `sqlx` pool or the table layout (spec §State Reader). It is intentionally
//! narrow: only the read stores the backend API consumes, exposed as
//! `Arc<dyn …Store>` so the backend couples to the typed contract, not the DB.

use std::sync::Arc;

use eos_types::{
    AgentRunStore, CoreError, ParentedAgentRunKind, ParentedRun, Request, RequestId, RequestStore,
    TaskAgentRunStore, TaskId, TaskRun, TaskStore, WorkflowId,
};

/// Request-level execution tree materialized from normalized lineage rows.
#[derive(Debug, Clone, PartialEq)]
pub struct RequestExecutionTree {
    /// The top-level request row.
    pub request: Request,
    /// The root task-agent-run node.
    pub root: TaskExecutionNode,
}

/// One task-agent-run node plus its direct child surfaces.
#[derive(Debug, Clone, PartialEq)]
pub struct TaskExecutionNode {
    /// The merged task and main run row.
    pub task_run: TaskRun,
    /// Direct subagent runs launched by this task's agent run.
    pub subagents: Vec<ParentedRun>,
    /// Direct advisor runs launched by this task's agent run.
    pub advisors: Vec<ParentedRun>,
    /// Workflows launched by this task's agent run.
    pub workflow_ids: Vec<WorkflowId>,
}

/// Read-side store handles exposed to the backend composition root.
///
/// Cheap to clone (every field is `Arc`-backed). Construct it through
/// [`AgentCoreRuntime::state_reader`](super::AgentCoreRuntime::state_reader).
#[derive(Clone)]
pub struct StateReader {
    requests: Arc<dyn RequestStore>,
    tasks: Arc<dyn TaskStore>,
    agent_runs: Arc<dyn AgentRunStore>,
    task_agent_runs: Arc<dyn TaskAgentRunStore>,
}

impl StateReader {
    pub(crate) fn new(
        requests: Arc<dyn RequestStore>,
        tasks: Arc<dyn TaskStore>,
        agent_runs: Arc<dyn AgentRunStore>,
        task_agent_runs: Arc<dyn TaskAgentRunStore>,
    ) -> Self {
        Self {
            requests,
            tasks,
            agent_runs,
            task_agent_runs,
        }
    }

    /// The request store (`list` / `get` / `finish_request`).
    #[must_use]
    pub fn requests(&self) -> Arc<dyn RequestStore> {
        self.requests.clone()
    }

    /// The task store (`list_for_request` / `get`).
    #[must_use]
    pub fn tasks(&self) -> Arc<dyn TaskStore> {
        self.tasks.clone()
    }

    /// The agent-run store (`get_for_task` / `get`).
    #[must_use]
    pub fn agent_runs(&self) -> Arc<dyn AgentRunStore> {
        self.agent_runs.clone()
    }

    /// The task-agent-run lineage store.
    #[must_use]
    pub fn task_agent_runs(&self) -> Arc<dyn TaskAgentRunStore> {
        self.task_agent_runs.clone()
    }

    /// Materialize the bounded v1 execution tree for one request.
    ///
    /// Returns `Ok(None)` when the request row is absent. A present request with
    /// no root task or missing root task-agent-run row is inconsistent lineage
    /// and returns a store error.
    ///
    /// # Errors
    /// Returns [`CoreError`] for store failures or inconsistent lineage.
    pub async fn request_execution_tree(
        &self,
        request_id: &RequestId,
    ) -> Result<Option<RequestExecutionTree>, CoreError> {
        let Some(request) = self.requests.get(request_id).await? else {
            return Ok(None);
        };
        let Some(root_task_id) = request.root_task_id.clone() else {
            return Err(CoreError::Store(format!(
                "request {} has no root_task_id",
                request.id.as_str()
            )));
        };
        let root = self.task_execution_node(&root_task_id).await?;
        Ok(Some(RequestExecutionTree { request, root }))
    }

    async fn task_execution_node(&self, task_id: &TaskId) -> Result<TaskExecutionNode, CoreError> {
        let Some(task_run) = self.task_agent_runs.get_task_run(task_id).await? else {
            return Err(CoreError::Store(format!(
                "task-agent-run {} not found",
                task_id.as_str()
            )));
        };
        let Some(index) = self.task_agent_runs.task_execution_index(task_id).await? else {
            return Err(CoreError::Store(format!(
                "task execution index {} not found",
                task_id.as_str()
            )));
        };
        let subagents = self
            .task_agent_runs
            .list_parented_runs_for_parent_task(task_id, ParentedAgentRunKind::Subagent)
            .await?;
        let advisors = self
            .task_agent_runs
            .list_parented_runs_for_parent_task(task_id, ParentedAgentRunKind::Advisor)
            .await?;
        Ok(TaskExecutionNode {
            task_run,
            subagents,
            advisors,
            workflow_ids: index.workflow_ids,
        })
    }
}

impl std::fmt::Debug for StateReader {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("StateReader").finish_non_exhaustive()
    }
}
