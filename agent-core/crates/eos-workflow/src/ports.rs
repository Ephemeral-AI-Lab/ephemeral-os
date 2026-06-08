use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use eos_ports::WorkflowControlPort;
use eos_state::{
    AttemptClosure, GeneratorSubmission, IterationStatus, ReducerSubmission, TaskStore, WorkflowId,
    WorkflowStatus,
};
use eos_tools::{
    AttemptSubmissionPort, CancelPort, OutstandingWorkflow, PlannerPlan, SubmissionAck, ToolError,
};
use eos_types::{AgentRunId, WorkflowSessionId};
use parking_lot::Mutex;

use crate::attempt::AttemptOrchestratorRegistry;
use crate::{WorkflowError, WorkflowStarter};

/// Recording adapter from the `eos-tools` planner/generator/reducer terminal
/// ports to the active per-attempt orchestrators (Path A-recording).
///
/// The submit tool writes the agent's real submission straight to the
/// orchestrator's non-advancing `record_*` variants and returns the
/// orchestrator's real ack; advancing the DAG stays the exclusive job of the
/// single `advance_run_stage` loop (D4: exactly one writer). This is the wired
/// implementor of [`AttemptSubmissionPort`], constructed once at the composition
/// root over the shared attempt registry.
#[derive(Clone)]
pub struct AttemptSubmissionAdapter {
    registry: Arc<AttemptOrchestratorRegistry>,
}

impl std::fmt::Debug for AttemptSubmissionAdapter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AttemptSubmissionAdapter")
            .finish_non_exhaustive()
    }
}

impl AttemptSubmissionAdapter {
    /// Create a submission adapter over the active attempt registry.
    #[must_use]
    pub fn new(registry: Arc<AttemptOrchestratorRegistry>) -> Self {
        Self { registry }
    }
}

impl eos_tools::ports::Sealed for AttemptSubmissionAdapter {}

#[async_trait]
impl AttemptSubmissionPort for AttemptSubmissionAdapter {
    async fn apply_plan(&self, plan: PlannerPlan) -> Result<SubmissionAck, ToolError> {
        let Some(orchestrator) = self.registry.get(&plan.attempt_id) else {
            return Ok(SubmissionAck::Rejected(format!(
                "attempt {:?} is not active",
                plan.attempt_id.as_str()
            )));
        };
        submission_ack(orchestrator.record_plan(plan).await)
    }

    async fn submit_generator(
        &self,
        submission: GeneratorSubmission,
    ) -> Result<SubmissionAck, ToolError> {
        let Some(orchestrator) = self.registry.get(&submission.attempt_id) else {
            return Ok(SubmissionAck::Rejected(format!(
                "attempt {:?} is not active",
                submission.attempt_id.as_str()
            )));
        };
        submission_ack(orchestrator.record_generator_submission(submission).await)
    }

    async fn apply_reducer(
        &self,
        submission: ReducerSubmission,
    ) -> Result<SubmissionAck, ToolError> {
        let Some(orchestrator) = self.registry.get(&submission.attempt_id) else {
            return Ok(SubmissionAck::Rejected(format!(
                "attempt {:?} is not active",
                submission.attempt_id.as_str()
            )));
        };
        submission_ack(orchestrator.record_reducer_submission(submission).await)
    }
}

fn submission_ack(result: crate::Result<()>) -> Result<SubmissionAck, ToolError> {
    match result {
        Ok(()) => Ok(SubmissionAck::Accepted),
        Err(WorkflowError::Store(err)) => Err(ToolError::Store(err)),
        Err(WorkflowError::Tool(err)) => Err(err),
        Err(WorkflowError::Join(err)) => Err(ToolError::Internal(err)),
        Err(err) => Ok(SubmissionAck::Rejected(err.to_string())),
    }
}

/// Adapter from `eos-tools` workflow-control ports to delegated workflow state.
#[derive(Clone)]
pub struct WorkflowControlAdapter {
    starter: WorkflowStarter,
    workflow_store: Arc<dyn eos_state::WorkflowStore>,
    iteration_store: Arc<dyn eos_state::IterationStore>,
    attempt_store: Arc<dyn eos_state::AttemptStore>,
    task_store: Arc<dyn TaskStore>,
    handles: Arc<WorkflowHandleRegistry>,
    /// The recursive cancellation port (spec §12.4): workflow cancellation
    /// decomposes through `cancel_task` rather than flipping task rows directly.
    cancel_port: Arc<dyn CancelPort>,
}

impl std::fmt::Debug for WorkflowControlAdapter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkflowControlAdapter")
            .finish_non_exhaustive()
    }
}

impl WorkflowControlAdapter {
    /// Create a workflow-control adapter.
    #[must_use]
    pub fn new(
        starter: WorkflowStarter,
        workflow_store: Arc<dyn eos_state::WorkflowStore>,
        iteration_store: Arc<dyn eos_state::IterationStore>,
        attempt_store: Arc<dyn eos_state::AttemptStore>,
        task_store: Arc<dyn TaskStore>,
        cancel_port: Arc<dyn CancelPort>,
    ) -> Self {
        Self {
            starter,
            workflow_store,
            iteration_store,
            attempt_store,
            task_store,
            handles: Arc::new(WorkflowHandleRegistry::default()),
            cancel_port,
        }
    }
}

impl eos_tools::ports::Sealed for WorkflowControlAdapter {}

#[async_trait]
impl WorkflowControlPort for WorkflowControlAdapter {
    async fn start(
        &self,
        parent_task_id: &eos_state::TaskId,
        _agent_run_id: &AgentRunId,
        workflow_goal: &str,
    ) -> Result<eos_tools::StartedWorkflowSession, ToolError> {
        let started = self
            .starter
            .start(workflow_goal, parent_task_id)
            .await
            .map_err(workflow_control_error)?;
        let workflow_task_id = self.handles.handle_for_workflow(&started.workflow_id)?;
        Ok(eos_tools::StartedWorkflowSession {
            workflow_task_id,
            workflow_id: started.workflow_id,
        })
    }

    async fn status(
        &self,
        workflow_id: &WorkflowId,
        workflow_task_id: Option<&WorkflowSessionId>,
    ) -> Result<String, ToolError> {
        if let Some(handle) = workflow_task_id {
            let Some(handle_workflow_id) = self.handles.workflow_id_for_handle(handle) else {
                return Ok(format!("Workflow handle {handle} was not found."));
            };
            if &handle_workflow_id != workflow_id {
                return Ok(format!(
                    "Workflow handle {handle} does not refer to workflow {workflow_id}."
                ));
            }
        }
        let Some(workflow) = self.workflow_store.get(workflow_id).await? else {
            return Ok(format!("Workflow {workflow_id} was not found."));
        };
        let handle = self.handles.handle_for_workflow(&workflow.id)?;
        let mut text = format!(
            "Workflow {} ({}) is {:?}. Goal: {}",
            workflow.id, handle, workflow.status, workflow.workflow_goal
        );
        if let Some(outcomes) = &workflow.outcomes {
            text.push_str("\nOutcomes:\n");
            text.push_str(outcomes);
        }
        Ok(text)
    }

    async fn cancel(
        &self,
        workflow_task_id: &WorkflowSessionId,
        reason: &str,
    ) -> Result<String, ToolError> {
        let Some(workflow_id) = self.handles.workflow_id_for_handle(workflow_task_id) else {
            return Ok(format!("Workflow handle {workflow_task_id} was not found."));
        };
        let Some(workflow) = self.workflow_store.get(&workflow_id).await? else {
            return Ok(format!("Workflow handle {workflow_task_id} was not found."));
        };
        if workflow.status != WorkflowStatus::Open {
            return Ok(format!(
                "Workflow {workflow_id} is already {:?}.",
                workflow.status
            ));
        }
        self.cancel_workflow_state(&workflow, reason).await?;
        Ok(format!("Workflow {workflow_id} cancelled: {reason}"))
    }

    async fn find_outstanding(
        &self,
        parent_task_id: &eos_state::TaskId,
        _agent_run_id: &AgentRunId,
    ) -> Result<Vec<OutstandingWorkflow>, ToolError> {
        self.workflow_store
            .list_for_parent_task(parent_task_id)
            .await?
            .into_iter()
            .filter(eos_state::Workflow::is_open)
            .map(|workflow| {
                Ok(OutstandingWorkflow {
                    workflow_task_id: self.handles.handle_for_workflow(&workflow.id)?,
                    workflow_id: workflow.id,
                    workflow_goal: workflow.workflow_goal,
                })
            })
            .collect()
    }

    async fn workflow_depth(&self, workflow_id: &WorkflowId) -> Result<u32, ToolError> {
        // Walk delegation ancestry via each workflow's parent task's owning
        // workflow (`task.workflow_id`), counting hops; 1 = top-level. The `seen`
        // guard stops a malformed cycle from looping forever (Rust parity).
        let mut depth: u32 = 1;
        let mut current = workflow_id.clone();
        let mut seen = std::collections::HashSet::new();
        while seen.insert(current.clone()) {
            let Some(workflow) = self.workflow_store.get(&current).await? else {
                break;
            };
            let Some(parent) = self.task_store.get(&workflow.parent_task_id).await? else {
                break;
            };
            match parent.workflow_id {
                Some(parent_workflow_id) => {
                    depth += 1;
                    current = parent_workflow_id;
                }
                None => break,
            }
        }
        Ok(depth)
    }
}

impl WorkflowControlAdapter {
    /// Decompose workflow cancellation through `cancel_iteration` -> `cancel_attempt`
    /// -> `cancel_task` (spec §12.4). Walks only *open* iterations / *non-closed*
    /// attempts (the idempotency guards), so a re-entrant cancel (a child workflow
    /// cancelled while tearing down a parent) terminates.
    async fn cancel_workflow_state(
        &self,
        workflow: &eos_state::Workflow,
        reason: &str,
    ) -> Result<(), ToolError> {
        let now = eos_state::UtcDateTime::now();
        // Iteration/workflow `outcomes` columns are read back strictly as
        // `Vec<ExecutionTaskOutcome>` by `ContextEngine`, so the cancellation
        // summary is the empty typed projection; the reason rides on each
        // cancelled task row and the `cancel` return string.
        const EMPTY_OUTCOMES: &str = "[]";

        for iteration in self.iteration_store.list_for_workflow(&workflow.id).await? {
            if !iteration.is_open() {
                continue;
            }
            for attempt in self.attempt_store.list_for_iteration(&iteration.id).await? {
                if attempt.is_closed() {
                    continue;
                }
                self.cancel_attempt(&attempt, reason, now).await?;
            }
            self.iteration_store
                .set_status(
                    &iteration.id,
                    IterationStatus::Cancelled,
                    Some(now),
                    Some(EMPTY_OUTCOMES),
                )
                .await?;
        }
        self.workflow_store
            .set_status(
                &workflow.id,
                WorkflowStatus::Cancelled,
                Some(now),
                Some(EMPTY_OUTCOMES),
            )
            .await?;
        Ok(())
    }

    /// Cancel one attempt (spec §12.4): latch every planner/generator/reducer task
    /// row to `Cancelled` *before* any teardown (closing the scheduler gap), then
    /// recurse `cancel_task` per task to tear down any live agent run, then close
    /// the attempt as `Cancelled`.
    async fn cancel_attempt(
        &self,
        attempt: &eos_state::Attempt,
        reason: &str,
        now: eos_state::UtcDateTime,
    ) -> Result<(), ToolError> {
        // Stop the planner orchestrator from materializing NEW (un-latched) task
        // rows. This is *not* redundant with `cancel_task(planner)`: the latch only
        // covers rows that exist at latch time, so the planner must be prevented
        // from creating fresh launchable rows after the latch.
        self.starter
            .orchestrator_registry()
            .abort_planner(&attempt.id);
        let tasks: Vec<eos_state::TaskId> = attempt
            .planner_task_id()
            .into_iter()
            .chain(attempt.generator_task_ids().iter())
            .chain(attempt.reducer_task_ids().iter())
            .cloned()
            .collect();
        // Latch BEFORE teardown so the scheduler sees terminal rows and cannot
        // launch a sibling into the cancellation window.
        self.task_store
            .latch_attempt_tasks_cancelled(&attempt.id, &tasks)
            .await?;
        // Tear down each task's live agent run. The status CAS inside `cancel_task`
        // no-ops (already latched `Cancelled`), but the live-run teardown still runs.
        for task_id in &tasks {
            self.cancel_port.cancel_task(task_id, reason).await?;
        }
        self.attempt_store
            .close(
                &attempt.id,
                AttemptClosure::Cancelled {
                    reason: reason.to_owned(),
                    outcomes: Vec::new(),
                    closed_at: now,
                },
            )
            .await?;
        Ok(())
    }
}

fn workflow_control_error(err: WorkflowError) -> ToolError {
    match err {
        WorkflowError::Store(err) => ToolError::Store(err),
        WorkflowError::Tool(err) => err,
        other => ToolError::Internal(other.to_string()),
    }
}

#[derive(Debug, Default)]
struct WorkflowHandleRegistry {
    next_handle: AtomicU64,
    inner: Mutex<WorkflowHandleMaps>,
}

#[derive(Debug, Default)]
struct WorkflowHandleMaps {
    workflow_by_handle: HashMap<WorkflowSessionId, WorkflowId>,
    handle_by_workflow: HashMap<WorkflowId, WorkflowSessionId>,
}

impl WorkflowHandleRegistry {
    fn handle_for_workflow(
        &self,
        workflow_id: &WorkflowId,
    ) -> Result<WorkflowSessionId, ToolError> {
        let mut guard = self.inner.lock();
        if let Some(handle) = guard.handle_by_workflow.get(workflow_id) {
            return Ok(handle.clone());
        }
        let id = self.next_handle.fetch_add(1, Ordering::Relaxed) + 1;
        let handle: WorkflowSessionId = format!("wf_{id}").parse()?;
        guard
            .workflow_by_handle
            .insert(handle.clone(), workflow_id.clone());
        guard
            .handle_by_workflow
            .insert(workflow_id.clone(), handle.clone());
        Ok(handle)
    }

    fn workflow_id_for_handle(&self, handle: &WorkflowSessionId) -> Option<WorkflowId> {
        self.inner.lock().workflow_by_handle.get(handle).cloned()
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use std::sync::Arc;

    use async_trait::async_trait;
    use eos_ports::WorkflowControlPort as _;
    use eos_state::{AttemptStatus, IterationStatus, TaskStatus, WorkflowStatus};
    use eos_types::JsonObject;
    use serde_json::json;

    use super::*;
    use crate::support::{root_task, MemoryStores, QueueRunner};

    /// A `CancelPort` fake mirroring `EngineCancelPort::cancel_task`'s persisted
    /// flip; these store-level tests have no live-run registry.
    struct TestCancelPort {
        task_store: Arc<dyn TaskStore>,
    }

    #[async_trait]
    impl CancelPort for TestCancelPort {
        async fn cancel_task(
            &self,
            task_id: &eos_state::TaskId,
            reason: &str,
        ) -> Result<(), ToolError> {
            if let Some(task) = self.task_store.get(task_id).await? {
                if matches!(task.status, TaskStatus::Pending | TaskStatus::Running) {
                    let mut terminal = JsonObject::new();
                    terminal.insert("fail_reason".to_owned(), "cancelled".into());
                    terminal.insert("reason".to_owned(), reason.into());
                    self.task_store
                        .set_task_status_if_current(
                            task_id,
                            task.status,
                            TaskStatus::Cancelled,
                            None,
                            Some(&terminal),
                        )
                        .await?;
                }
            }
            Ok(())
        }

        async fn cancel_agent_run(
            &self,
            _agent_run_id: &AgentRunId,
            _reason: &str,
        ) -> Result<(), ToolError> {
            Ok(())
        }
    }

    // The workflow-control adapter mints `wf_<n>` handles (not workflow ids),
    // rejects a fabricated handle, and `cancel` decomposes through the delegated
    // tree (workflow + iteration + attempt CANCELLED, active tasks CANCELLED with a
    // `cancelled` marker, latched before close) without mutating the parent.
    #[tokio::test]
    async fn workflow_control_uses_runtime_handles_and_cancels_child_state() {
        let stores = Arc::new(MemoryStores::default());
        let runner = Arc::new(QueueRunner::default());
        let deps = stores.deps(runner);
        let parent = root_task("parent", TaskStatus::Running);
        stores.seed_task(parent.clone());
        let cancel_port: Arc<dyn CancelPort> = Arc::new(TestCancelPort {
            task_store: stores.clone(),
        });
        let adapter = WorkflowControlAdapter::new(
            WorkflowStarter::new(deps),
            stores.clone(),
            stores.clone(),
            stores.clone(),
            stores.clone(),
            cancel_port,
        );

        let agent_run_id: AgentRunId = "agent-run-1".parse().expect("agent run id");
        let started = adapter
            .start(&parent.id, &agent_run_id, "delegated goal")
            .await
            .unwrap();
        assert_eq!(started.workflow_task_id.as_str(), "wf_1");
        let derived_handle: eos_types::WorkflowSessionId =
            format!("wf_{}", started.workflow_id.as_str())
                .parse()
                .unwrap();
        assert!(adapter
            .status(&started.workflow_id, Some(&derived_handle))
            .await
            .unwrap()
            .contains("was not found"));

        adapter
            .cancel(&started.workflow_task_id, "stop now")
            .await
            .unwrap();

        let workflow = stores.workflow(&started.workflow_id).unwrap();
        assert_eq!(workflow.status, WorkflowStatus::Cancelled);
        let iteration_id = workflow.iteration_ids.first().unwrap();
        let iteration = stores.iteration(iteration_id).unwrap();
        assert_eq!(iteration.status, IterationStatus::Cancelled);
        let attempt_id = iteration.attempt_ids.first().unwrap();
        let attempt = stores.attempt(attempt_id).unwrap();
        assert_eq!(attempt.status(), AttemptStatus::Cancelled);
        assert!(attempt.fail_reason().is_none());
        let planner_task = stores.task(attempt.planner_task_id().unwrap()).unwrap();
        assert_eq!(planner_task.status, TaskStatus::Cancelled);
        assert_eq!(
            planner_task
                .terminal_tool_result
                .unwrap()
                .get("fail_reason"),
            Some(&json!("cancelled"))
        );
        // `cancel_workflow` must never mutate the parent task (anchor §3).
        assert_eq!(stores.task(&parent.id).unwrap().status, TaskStatus::Running);
    }
}
