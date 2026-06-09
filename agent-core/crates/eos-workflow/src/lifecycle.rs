use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use eos_types::{
    AgentRunId, IterationCreationReason, IterationOutcome, IterationStatus, IterationStore,
    ToolUseId, Workflow, WorkflowId, WorkflowOutcome,
};

use crate::attempt::AttemptResources;
use crate::ids::WorkflowLifecycleConfig;
use crate::iteration::{
    IterationAttemptCoordinator, IterationClosed, IterationClosedCallback,
    OpenIterationCoordinatorRegistry,
};
use crate::{Result, WorkflowError};

type IterationCoordinatorFuture<'a> = Pin<
    Box<
        dyn Future<Output = Result<(eos_types::Iteration, Arc<IterationAttemptCoordinator>)>>
            + Send
            + 'a,
    >,
>;

/// Workflow-level lifecycle coordinator.
#[derive(Clone)]
pub(crate) struct WorkflowLifecycle {
    deps: AttemptResources,
    iteration_coordinators: Arc<OpenIterationCoordinatorRegistry>,
    config: WorkflowLifecycleConfig,
}

impl std::fmt::Debug for WorkflowLifecycle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WorkflowLifecycle")
            .field("config", &self.config)
            .finish_non_exhaustive()
    }
}

impl WorkflowLifecycle {
    /// Create a lifecycle coordinator.
    #[must_use]
    pub(crate) fn new(
        deps: AttemptResources,
        iteration_coordinators: Arc<OpenIterationCoordinatorRegistry>,
    ) -> Self {
        Self {
            config: deps.lifecycle_config,
            deps,
            iteration_coordinators,
        }
    }

    /// Insert a workflow row.
    pub(crate) async fn create_workflow(
        &self,
        request_id: &eos_types::RequestId,
        parent_task_id: &eos_types::TaskId,
        launched_by_agent_run_id: &AgentRunId,
        tool_use_id: Option<&ToolUseId>,
        workflow_goal: &str,
    ) -> Result<Workflow> {
        Ok(self
            .deps
            .workflow_store
            .insert(
                request_id,
                parent_task_id,
                launched_by_agent_run_id,
                tool_use_id,
                workflow_goal,
            )
            .await?)
    }

    /// Create the next iteration and register its coordinator.
    pub(crate) fn create_iteration_with_coordinator<'a>(
        &'a self,
        workflow_id: &'a WorkflowId,
    ) -> IterationCoordinatorFuture<'a> {
        Box::pin(self.create_iteration_with_coordinator_inner(workflow_id))
    }

    async fn create_iteration_with_coordinator_inner(
        &self,
        workflow_id: &WorkflowId,
    ) -> Result<(eos_types::Iteration, Arc<IterationAttemptCoordinator>)> {
        let workflow = self.require_workflow(workflow_id).await?;
        if !workflow.is_open() {
            return Err(WorkflowError::invariant(format!(
                "workflow {:?} is not open",
                workflow.id.as_str()
            )));
        }
        let (sequence_no, reason, goal) = if workflow.iteration_ids.is_empty() {
            (
                1,
                IterationCreationReason::Initial,
                workflow.workflow_goal.clone(),
            )
        } else {
            let previous_id = workflow
                .iteration_ids
                .last()
                .ok_or_else(|| WorkflowError::invariant("workflow has no iterations"))?
                .clone();
            let previous = self
                .deps
                .iteration_store
                .get(&previous_id)
                .await?
                .ok_or_else(|| WorkflowError::not_found("iteration", previous_id.as_str()))?;
            if previous.status != IterationStatus::Succeeded {
                return Err(WorkflowError::invariant(format!(
                    "continuation requires predecessor iteration {:?} to be succeeded",
                    previous.id.as_str()
                )));
            }
            let goal = previous
                .deferred_goal_for_next_iteration
                .clone()
                .ok_or_else(|| {
                    WorkflowError::invariant(format!(
                        "iteration {:?} has no deferred goal",
                        previous.id.as_str()
                    ))
                })?;
            (
                previous.sequence_no + 1,
                IterationCreationReason::DeferredGoalContinuation,
                goal.into_string(),
            )
        };
        let expected = workflow.iteration_ids.len() as i64 + 1;
        if sequence_no != expected {
            return Err(WorkflowError::invariant(format!(
                "iteration sequence_no must be contiguous: expected {expected}, got {sequence_no}"
            )));
        }
        let iteration = self
            .deps
            .iteration_store
            .insert(
                &workflow.id,
                sequence_no,
                reason,
                &goal,
                self.config.default_attempt_budget,
            )
            .await?;
        self.deps
            .workflow_store
            .append_iteration_id(&workflow.id, &iteration.id)
            .await?;

        let lifecycle = self.clone();
        let callback: IterationClosedCallback = Arc::new(move |closed: IterationClosed| {
            let lifecycle = lifecycle.clone();
            Box::pin(async move { lifecycle.handle_iteration_closed(closed).await })
        });
        let coordinator =
            IterationAttemptCoordinator::new(iteration.id.clone(), self.deps.clone(), callback);
        self.iteration_coordinators.register(coordinator.clone())?;
        Ok((iteration, coordinator))
    }

    /// React to a closed iteration.
    pub(crate) async fn handle_iteration_closed(&self, closed: IterationClosed) -> Result<()> {
        let iteration = self
            .deps
            .iteration_store
            .get(&closed.iteration_id)
            .await?
            .ok_or_else(|| WorkflowError::not_found("iteration", closed.iteration_id.as_str()))?;
        // The closed iteration's coordinator has finished its job; release it up
        // front so no early return below can leak it (mirrors Rust's `finally`).
        self.iteration_coordinators.deregister(&iteration.id);

        match closed.outcome {
            IterationOutcome::Continue { .. } => {
                self.start_iteration_with_deferred_goal(&iteration.workflow_id)
                    .await
            }
            IterationOutcome::Complete => self
                .close_workflow(&iteration.workflow_id, WorkflowOutcome::Succeeded)
                .await
                .map(|_| ()),
            IterationOutcome::Failed | IterationOutcome::Cancelled { .. } => self
                .close_workflow(&iteration.workflow_id, WorkflowOutcome::Failed)
                .await
                .map(|_| ()),
        }
    }

    /// Start the deferred-goal continuation iteration, compensating on a
    /// first-attempt start failure (parity with Rust `_start_deferred_iteration`).
    async fn start_iteration_with_deferred_goal(&self, workflow_id: &WorkflowId) -> Result<()> {
        let (next, coordinator) = self.create_iteration_with_coordinator(workflow_id).await?;
        if let Err(err) = coordinator.create_and_start_first_attempt().await {
            // Continuation could not start: cancel the new iteration, release its
            // coordinator, and fail the workflow. The original error is swallowed
            // after compensation, exactly as Rust's `except` does — but log it
            // first (parity with `_start_deferred_iteration`'s `logger.exception`),
            // since the FAILED workflow status is otherwise the only trace.
            tracing::warn!(
                error = %err,
                workflow_id = %workflow_id.as_str(),
                iteration_id = %next.id.as_str(),
                "continuation first-attempt start failed; compensating workflow to FAILED",
            );
            self.iteration_coordinators.deregister(&next.id);
            self.deps
                .iteration_store
                .set_status(
                    &next.id,
                    IterationStatus::Cancelled,
                    Some(eos_types::UtcDateTime::now()),
                    None,
                )
                .await?;
            self.close_workflow(workflow_id, WorkflowOutcome::Failed)
                .await?;
        }
        Ok(())
    }

    /// Close a workflow without mutating the parent task.
    pub async fn close_workflow(
        &self,
        workflow_id: &WorkflowId,
        outcome: WorkflowOutcome,
    ) -> Result<Workflow> {
        let workflow = self.require_workflow(workflow_id).await?;
        if !workflow.is_open() {
            return Err(WorkflowError::invariant(format!(
                "workflow {:?} is not open",
                workflow.id.as_str()
            )));
        }
        let outcomes =
            workflow_outcomes_json(self.deps.iteration_store.as_ref(), &workflow).await?;
        Ok(self
            .deps
            .workflow_store
            .set_status(
                workflow_id,
                outcome.status(),
                Some(eos_types::UtcDateTime::now()),
                Some(&outcomes),
            )
            .await?)
    }

    async fn require_workflow(&self, workflow_id: &WorkflowId) -> Result<Workflow> {
        self.deps
            .workflow_store
            .get(workflow_id)
            .await?
            .ok_or_else(|| WorkflowError::not_found("workflow", workflow_id.as_str()))
    }
}

async fn workflow_outcomes_json(
    iteration_store: &dyn IterationStore,
    workflow: &Workflow,
) -> Result<String> {
    let iterations = iteration_store.list_for_workflow(&workflow.id).await?;
    let Some(latest) = iterations
        .iter()
        .max_by_key(|iteration| iteration.sequence_no)
    else {
        return Ok("[]".to_owned());
    };
    Ok(latest.outcomes.clone().unwrap_or_else(|| "[]".to_owned()))
}

#[cfg(test)]
#[path = "../tests/lifecycle/mod.rs"]
mod tests;
