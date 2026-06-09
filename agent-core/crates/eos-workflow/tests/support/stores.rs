//! In-memory `Store` trait implementations (`MemoryStores`) plus the small
//! seed/lookup helpers the per-module AC tests build on.
#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{
    AgentRunId, Attempt, AttemptBudget, AttemptClosure, AttemptId, AttemptState, CoreError,
    DeferredGoal, ExecutionTaskOutcome, Iteration, IterationCreationReason, IterationId,
    IterationStatus, JsonObject, MaterializedPlan, RequestId, Task, TaskId, TaskStatus, ToolUseId,
    Workflow, WorkflowId, WorkflowStatus,
};
use parking_lot::Mutex;

use crate::attempt::{AgentRunner, AttemptOrchestratorRegistry, AttemptResources};
use crate::iteration::OpenIterationCoordinatorRegistry;

use super::runners::agent_registry;

#[derive(Debug, Default)]
pub(crate) struct MemoryStores {
    workflows: Mutex<HashMap<WorkflowId, Workflow>>,
    iterations: Mutex<HashMap<IterationId, Iteration>>,
    attempts: Mutex<HashMap<AttemptId, Attempt>>,
    tasks: Mutex<HashMap<TaskId, Task>>,
    /// Count of every mutating `TaskStore` call (AC-eos-workflow-05).
    task_writes: AtomicUsize,
}

impl MemoryStores {
    pub(crate) fn deps(self: &Arc<Self>, runner: Arc<dyn AgentRunner>) -> AttemptResources {
        let store = Arc::clone(self);
        let mut deps = AttemptResources::new(
            store.clone(),
            store.clone(),
            store.clone(),
            store,
            Arc::new(agent_registry()),
            runner,
        );
        deps.orchestrator_registry = Arc::new(AttemptOrchestratorRegistry::new());
        deps.iteration_coordinators = Some(Arc::new(OpenIterationCoordinatorRegistry::new()));
        deps.max_concurrent_task_runs = 2;
        deps
    }

    pub(crate) fn seed_task(&self, task: Task) {
        self.tasks.lock().insert(task.id.clone(), task);
    }

    pub(crate) fn task(&self, id: &TaskId) -> Option<Task> {
        self.tasks.lock().get(id).cloned()
    }

    pub(crate) fn workflow(&self, id: &WorkflowId) -> Option<Workflow> {
        self.workflows.lock().get(id).cloned()
    }

    pub(crate) fn iteration(&self, id: &IterationId) -> Option<Iteration> {
        self.iterations.lock().get(id).cloned()
    }

    pub(crate) fn attempt(&self, id: &AttemptId) -> Option<Attempt> {
        self.attempts.lock().get(id).cloned()
    }

    /// Total mutating `TaskStore` calls observed so far.
    pub(crate) fn task_write_count(&self) -> usize {
        self.task_writes.load(Ordering::Relaxed)
    }

    // Direct seeders for context/lifecycle tests (the trait `insert`/`get`
    // methods are ambiguous across the four store traits; these inherent
    // wrappers keep test bodies readable and do not touch `task_writes`).

    pub(crate) async fn seed_workflow(&self, goal: &str) -> Workflow {
        eos_types::WorkflowStore::insert(
            self,
            &RequestId::new_v4(),
            &tid("root"),
            &AgentRunId::new_v4(),
            None,
            goal,
        )
        .await
        .unwrap()
    }

    pub(crate) async fn seed_iteration(
        &self,
        workflow_id: &WorkflowId,
        sequence_no: i64,
        creation_reason: IterationCreationReason,
        iteration_goal: &str,
        attempt_budget: AttemptBudget,
    ) -> Iteration {
        let iteration = eos_types::IterationStore::insert(
            self,
            workflow_id,
            sequence_no,
            creation_reason,
            iteration_goal,
            attempt_budget,
        )
        .await
        .unwrap();
        eos_types::WorkflowStore::append_iteration_id(self, workflow_id, &iteration.id)
            .await
            .unwrap();
        iteration
    }

    pub(crate) async fn seed_attempt(
        &self,
        iteration_id: &IterationId,
        workflow_id: &WorkflowId,
        sequence_no: i64,
    ) -> Attempt {
        let attempt = eos_types::AttemptStore::insert(self, iteration_id, workflow_id, sequence_no)
            .await
            .unwrap();
        eos_types::IterationStore::append_attempt_id(self, iteration_id, &attempt.id)
            .await
            .unwrap();
        attempt
    }
}

pub(crate) fn tid(id: &str) -> TaskId {
    id.parse().unwrap()
}

impl eos_types::Sealed for MemoryStores {}

#[async_trait]
impl eos_types::WorkflowStore for MemoryStores {
    async fn insert(
        &self,
        request_id: &RequestId,
        parent_task_id: &TaskId,
        launched_by_agent_run_id: &AgentRunId,
        tool_use_id: Option<&ToolUseId>,
        workflow_goal: &str,
    ) -> std::result::Result<Workflow, CoreError> {
        let now = eos_types::UtcDateTime::now();
        let workflow = Workflow {
            id: WorkflowId::new_v4(),
            request_id: request_id.clone(),
            workflow_goal: workflow_goal.to_owned(),
            status: WorkflowStatus::Open,
            iteration_ids: Vec::new(),
            parent_task_id: parent_task_id.clone(),
            launched_by_agent_run_id: launched_by_agent_run_id.clone(),
            tool_use_id: tool_use_id.cloned(),
            outcomes: None,
            created_at: now,
            updated_at: now,
            closed_at: None,
        };
        self.workflows
            .lock()
            .insert(workflow.id.clone(), workflow.clone());
        Ok(workflow)
    }

    async fn get(&self, id: &WorkflowId) -> std::result::Result<Option<Workflow>, CoreError> {
        Ok(self.workflows.lock().get(id).cloned())
    }

    async fn append_iteration_id(
        &self,
        id: &WorkflowId,
        iteration_id: &IterationId,
    ) -> std::result::Result<Workflow, CoreError> {
        let mut guard = self.workflows.lock();
        let workflow = guard
            .get_mut(id)
            .ok_or_else(|| not_found("workflow", id.as_str()))?;
        workflow.iteration_ids.push(iteration_id.clone());
        workflow.updated_at = eos_types::UtcDateTime::now();
        Ok(workflow.clone())
    }

    async fn set_status(
        &self,
        id: &WorkflowId,
        status: WorkflowStatus,
        closed_at: Option<eos_types::UtcDateTime>,
        outcomes: Option<&str>,
    ) -> std::result::Result<Workflow, CoreError> {
        let mut guard = self.workflows.lock();
        let workflow = guard
            .get_mut(id)
            .ok_or_else(|| not_found("workflow", id.as_str()))?;
        workflow.status = status;
        workflow.closed_at = closed_at;
        workflow.updated_at = eos_types::UtcDateTime::now();
        if let Some(outcomes) = outcomes {
            workflow.outcomes = Some(outcomes.to_owned());
        }
        Ok(workflow.clone())
    }

    async fn list_for_parent_task(
        &self,
        parent_task_id: &TaskId,
    ) -> std::result::Result<Vec<Workflow>, CoreError> {
        let mut workflows: Vec<Workflow> = self
            .workflows
            .lock()
            .values()
            .filter(|workflow| &workflow.parent_task_id == parent_task_id)
            .cloned()
            .collect();
        workflows.sort_by_key(|workflow| workflow.created_at);
        Ok(workflows)
    }

    async fn list_for_launching_agent_run(
        &self,
        launched_by_agent_run_id: &AgentRunId,
    ) -> std::result::Result<Vec<Workflow>, CoreError> {
        let mut workflows: Vec<Workflow> = self
            .workflows
            .lock()
            .values()
            .filter(|workflow| &workflow.launched_by_agent_run_id == launched_by_agent_run_id)
            .cloned()
            .collect();
        workflows.sort_by_key(|workflow| workflow.created_at);
        Ok(workflows)
    }

    async fn cancel_open_workflows_for_request(
        &self,
        request_id: &RequestId,
        reason: &str,
    ) -> std::result::Result<usize, CoreError> {
        let now = eos_types::UtcDateTime::now();
        let outcomes = cancellation_outcomes(reason);
        let mut count = 0;
        for workflow in self.workflows.lock().values_mut() {
            if &workflow.request_id == request_id && workflow.status == WorkflowStatus::Open {
                workflow.status = WorkflowStatus::Cancelled;
                workflow.outcomes.get_or_insert_with(|| outcomes.clone());
                workflow.closed_at.get_or_insert(now);
                workflow.updated_at = now;
                count += 1;
            }
        }
        Ok(count)
    }
}

#[async_trait]
impl eos_types::IterationStore for MemoryStores {
    async fn insert(
        &self,
        workflow_id: &WorkflowId,
        sequence_no: i64,
        creation_reason: IterationCreationReason,
        iteration_goal: &str,
        attempt_budget: AttemptBudget,
    ) -> std::result::Result<Iteration, CoreError> {
        let now = eos_types::UtcDateTime::now();
        let iteration = Iteration {
            id: IterationId::new_v4(),
            workflow_id: workflow_id.clone(),
            sequence_no,
            creation_reason,
            iteration_goal: iteration_goal.to_owned(),
            attempt_budget,
            status: IterationStatus::Open,
            attempt_ids: Vec::new(),
            deferred_goal_for_next_iteration: None,
            created_at: now,
            updated_at: now,
            closed_at: None,
            outcomes: None,
        };
        self.iterations
            .lock()
            .insert(iteration.id.clone(), iteration.clone());
        Ok(iteration)
    }

    async fn get(&self, id: &IterationId) -> std::result::Result<Option<Iteration>, CoreError> {
        Ok(self.iterations.lock().get(id).cloned())
    }

    async fn append_attempt_id(
        &self,
        id: &IterationId,
        attempt_id: &AttemptId,
    ) -> std::result::Result<Iteration, CoreError> {
        let mut guard = self.iterations.lock();
        let iteration = guard
            .get_mut(id)
            .ok_or_else(|| not_found("iteration", id.as_str()))?;
        iteration.attempt_ids.push(attempt_id.clone());
        iteration.updated_at = eos_types::UtcDateTime::now();
        Ok(iteration.clone())
    }

    async fn set_status(
        &self,
        id: &IterationId,
        status: IterationStatus,
        closed_at: Option<eos_types::UtcDateTime>,
        outcomes: Option<&str>,
    ) -> std::result::Result<Iteration, CoreError> {
        let mut guard = self.iterations.lock();
        let iteration = guard
            .get_mut(id)
            .ok_or_else(|| not_found("iteration", id.as_str()))?;
        iteration.status = status;
        iteration.closed_at = closed_at;
        iteration.updated_at = eos_types::UtcDateTime::now();
        if let Some(outcomes) = outcomes {
            iteration.outcomes = Some(outcomes.to_owned());
        }
        Ok(iteration.clone())
    }

    async fn set_deferred_goal_for_next_iteration(
        &self,
        id: &IterationId,
        deferred_goal_for_next_iteration: Option<&DeferredGoal>,
    ) -> std::result::Result<Iteration, CoreError> {
        let mut guard = self.iterations.lock();
        let iteration = guard
            .get_mut(id)
            .ok_or_else(|| not_found("iteration", id.as_str()))?;
        iteration.deferred_goal_for_next_iteration = deferred_goal_for_next_iteration.cloned();
        iteration.updated_at = eos_types::UtcDateTime::now();
        Ok(iteration.clone())
    }

    async fn close_succeeded(
        &self,
        id: &IterationId,
        outcomes: &str,
        closed_at: Option<eos_types::UtcDateTime>,
    ) -> std::result::Result<Iteration, CoreError> {
        self.set_status(id, IterationStatus::Succeeded, closed_at, Some(outcomes))
            .await
    }

    async fn list_for_workflow(
        &self,
        workflow_id: &WorkflowId,
    ) -> std::result::Result<Vec<Iteration>, CoreError> {
        let mut iterations: Vec<Iteration> = self
            .iterations
            .lock()
            .values()
            .filter(|iteration| &iteration.workflow_id == workflow_id)
            .cloned()
            .collect();
        iterations.sort_by_key(|iteration| iteration.sequence_no);
        Ok(iterations)
    }

    async fn cancel_open_iterations_for_request(
        &self,
        request_id: &RequestId,
        reason: &str,
    ) -> std::result::Result<usize, CoreError> {
        let workflow_ids = workflow_ids_for_request(&self.workflows.lock(), request_id);
        let now = eos_types::UtcDateTime::now();
        let outcomes = cancellation_outcomes(reason);
        let mut count = 0;
        for iteration in self.iterations.lock().values_mut() {
            if workflow_ids.contains(&iteration.workflow_id)
                && iteration.status == IterationStatus::Open
            {
                iteration.status = IterationStatus::Cancelled;
                iteration.outcomes.get_or_insert_with(|| outcomes.clone());
                iteration.closed_at.get_or_insert(now);
                iteration.updated_at = now;
                count += 1;
            }
        }
        Ok(count)
    }
}

#[async_trait]
impl eos_types::AttemptStore for MemoryStores {
    async fn insert(
        &self,
        iteration_id: &IterationId,
        workflow_id: &WorkflowId,
        attempt_sequence_no: i64,
    ) -> std::result::Result<Attempt, CoreError> {
        let now = eos_types::UtcDateTime::now();
        let attempt = Attempt {
            id: AttemptId::new_v4(),
            iteration_id: iteration_id.clone(),
            workflow_id: workflow_id.clone(),
            attempt_sequence_no,
            state: AttemptState::Planning {
                planner_task_id: None,
            },
            created_at: now,
            updated_at: now,
        };
        self.attempts
            .lock()
            .insert(attempt.id.clone(), attempt.clone());
        Ok(attempt)
    }

    async fn get(&self, id: &AttemptId) -> std::result::Result<Option<Attempt>, CoreError> {
        Ok(self.attempts.lock().get(id).cloned())
    }

    async fn record_planner_task(
        &self,
        id: &AttemptId,
        planner_task_id: &TaskId,
    ) -> std::result::Result<Attempt, CoreError> {
        let mut guard = self.attempts.lock();
        let attempt = guard
            .get_mut(id)
            .ok_or_else(|| not_found("attempt", id.as_str()))?;
        attempt.state = AttemptState::Planning {
            planner_task_id: Some(planner_task_id.clone()),
        };
        attempt.updated_at = eos_types::UtcDateTime::now();
        Ok(attempt.clone())
    }

    async fn record_plan(
        &self,
        id: &AttemptId,
        plan: &MaterializedPlan,
    ) -> std::result::Result<Attempt, CoreError> {
        let mut guard = self.attempts.lock();
        let attempt = guard
            .get_mut(id)
            .ok_or_else(|| not_found("attempt", id.as_str()))?;
        attempt.state = AttemptState::Running { plan: plan.clone() };
        attempt.updated_at = eos_types::UtcDateTime::now();
        Ok(attempt.clone())
    }

    async fn close(
        &self,
        id: &AttemptId,
        closure: AttemptClosure,
    ) -> std::result::Result<Attempt, CoreError> {
        let mut guard = self.attempts.lock();
        let attempt = guard
            .get_mut(id)
            .ok_or_else(|| not_found("attempt", id.as_str()))?;
        let planner_task_id = attempt.planner_task_id().cloned();
        let plan = attempt.materialized_plan().cloned();
        let planner_task_id = if plan.is_some() {
            None
        } else {
            planner_task_id
        };
        attempt.state = AttemptState::Closed {
            closure,
            planner_task_id,
            plan,
        };
        attempt.updated_at = eos_types::UtcDateTime::now();
        Ok(attempt.clone())
    }

    async fn list_for_iteration(
        &self,
        iteration_id: &IterationId,
    ) -> std::result::Result<Vec<Attempt>, CoreError> {
        let mut attempts: Vec<Attempt> = self
            .attempts
            .lock()
            .values()
            .filter(|attempt| &attempt.iteration_id == iteration_id)
            .cloned()
            .collect();
        attempts.sort_by_key(|attempt| attempt.attempt_sequence_no);
        Ok(attempts)
    }

    async fn cancel_open_attempts_for_request(
        &self,
        request_id: &RequestId,
    ) -> std::result::Result<usize, CoreError> {
        let workflow_ids = workflow_ids_for_request(&self.workflows.lock(), request_id);
        let mut count = 0;
        for attempt in self.attempts.lock().values_mut() {
            if workflow_ids.contains(&attempt.workflow_id) && !attempt.is_closed() {
                let planner_task_id = attempt.planner_task_id().cloned();
                let plan = attempt.materialized_plan().cloned();
                let planner_task_id = if plan.is_some() {
                    None
                } else {
                    planner_task_id
                };
                attempt.state = AttemptState::Closed {
                    closure: AttemptClosure::Cancelled {
                        reason: String::new(),
                        outcomes: Vec::new(),
                        closed_at: eos_types::UtcDateTime::now(),
                    },
                    planner_task_id,
                    plan,
                };
                attempt.updated_at = eos_types::UtcDateTime::now();
                count += 1;
            }
        }
        Ok(count)
    }
}

#[async_trait]
impl eos_types::TaskStore for MemoryStores {
    async fn insert_task(&self, task: &Task) -> std::result::Result<(), CoreError> {
        self.task_writes.fetch_add(1, Ordering::Relaxed);
        let mut tasks = self.tasks.lock();
        if tasks.contains_key(&task.id) {
            return Err(CoreError::Store(format!("task {} already exists", task.id)));
        }
        tasks.insert(task.id.clone(), task.clone());
        Ok(())
    }

    async fn get(&self, id: &TaskId) -> std::result::Result<Option<Task>, CoreError> {
        Ok(self.tasks.lock().get(id).cloned())
    }

    async fn set_task_status_if_current(
        &self,
        id: &TaskId,
        expected: TaskStatus,
        status: TaskStatus,
        outcomes: Option<&[ExecutionTaskOutcome]>,
        terminal_payload: Option<&JsonObject>,
    ) -> std::result::Result<Option<Task>, CoreError> {
        self.task_writes.fetch_add(1, Ordering::Relaxed);
        let mut guard = self.tasks.lock();
        let task = guard
            .get_mut(id)
            .ok_or_else(|| not_found("task", id.as_str()))?;
        if task.status != expected {
            return Ok(None);
        }
        update_task(task, status, outcomes, terminal_payload);
        Ok(Some(task.clone()))
    }

    async fn latch_attempt_tasks_cancelled(
        &self,
        attempt_id: &AttemptId,
        ids: &[TaskId],
    ) -> std::result::Result<(), CoreError> {
        let mut guard = self.tasks.lock();
        let mut terminal = JsonObject::new();
        terminal.insert("fail_reason".to_owned(), "cancelled".into());
        for id in ids {
            if let Some(task) = guard.get_mut(id) {
                if task.attempt_id.as_ref() == Some(attempt_id)
                    && matches!(task.status, TaskStatus::Pending | TaskStatus::Running)
                {
                    self.task_writes.fetch_add(1, Ordering::Relaxed);
                    update_task(task, TaskStatus::Cancelled, None, Some(&terminal));
                }
            }
        }
        Ok(())
    }

    async fn list_for_request(
        &self,
        request_id: &RequestId,
    ) -> std::result::Result<Vec<Task>, CoreError> {
        let mut tasks: Vec<Task> = self
            .tasks
            .lock()
            .values()
            .filter(|task| &task.request_id == request_id)
            .cloned()
            .collect();
        tasks.sort_by(|a, b| a.id.as_str().cmp(b.id.as_str()));
        Ok(tasks)
    }
}

fn update_task(
    task: &mut Task,
    status: TaskStatus,
    outcomes: Option<&[ExecutionTaskOutcome]>,
    terminal_payload: Option<&JsonObject>,
) {
    task.status = status;
    if let Some(outcomes) = outcomes {
        task.outcomes = outcomes.to_vec();
    }
    if let Some(result) = terminal_payload {
        task.terminal_payload = Some(result.clone());
    }
}

fn workflow_ids_for_request(
    workflows: &HashMap<WorkflowId, Workflow>,
    request_id: &RequestId,
) -> Vec<WorkflowId> {
    workflows
        .values()
        .filter(|workflow| &workflow.request_id == request_id)
        .map(|workflow| workflow.id.clone())
        .collect()
}

fn cancellation_outcomes(reason: &str) -> String {
    serde_json::json!([{
        "status": "cancelled",
        "reason": reason,
    }])
    .to_string()
}

fn not_found(entity: &str, id: &str) -> CoreError {
    CoreError::Store(format!("{entity} {id} not found"))
}
