use std::sync::Arc;

use eos_types::{
    execution_outcome_for_submission, Attempt, ExecutionRole, GeneratorSubmission, JsonObject,
    PlannerFailReason, PlannerFailureSubmission, ReducerSubmission, Task, TaskOutcomeStatus,
    TaskRole, TaskStatus,
};
use serde_json::{json, Value};
use tokio::task::JoinSet;

use crate::attempt::plan_dag::{dag_resolution, ready_pending_plan_ids, DagResolution};
use crate::attempt::{AgentLaunch, AgentLaunchFactory, AgentRunReport, AttemptResources};
use crate::util::json_object;
use crate::{Result, WorkflowError};

/// Workflow diagnostic event-type constants (Rust `workflow._core.audit`).
const TASK_READY: &str = "workflow.task.ready";
const TASK_LAUNCHED: &str = "workflow.task.launched";
const TASK_FAILED: &str = "workflow.task.failed";

use super::AttemptOrchestrator;

/// Single-writer RUN-stage scheduler for one Attempt.
#[derive(Debug, Clone)]
pub struct AttemptStageAdvancer {
    orchestrator: Arc<AttemptOrchestrator>,
}

impl AttemptStageAdvancer {
    /// Create a scheduler for an orchestrator.
    #[must_use]
    pub fn new(orchestrator: Arc<AttemptOrchestrator>) -> Self {
        Self { orchestrator }
    }

    /// Drive RUN-stage tasks to quiescence or until no locally-spawned running
    /// task is available to join.
    ///
    /// # Errors
    /// Returns [`WorkflowError`] for persisted DAG/store invariants.
    pub async fn advance_run_stage(&self) -> Result<()> {
        let deps = self.orchestrator.deps().clone();
        if deps.max_concurrent_task_runs == 0 {
            return Err(WorkflowError::invariant(
                "max_concurrent_task_runs must be at least 1",
            ));
        }
        let mut set = JoinSet::new();
        loop {
            let attempt = self.orchestrator.fresh_attempt().await?;
            if attempt.is_closed() || attempt.stage() != eos_types::AttemptStage::Run {
                return Ok(());
            }
            let tasks = self.orchestrator.plan_task_records(&attempt).await?;
            for task_id in ready_pending_plan_ids(&tasks)? {
                if set.len() >= deps.max_concurrent_task_runs {
                    break;
                }
                // D8: task_ready (pending -> pending) on the still-pending row,
                // then task_launched (pending -> running) on the transition.
                if let Some(pending) = tasks.iter().find(|task| task.id == task_id) {
                    let needs: Vec<&str> = pending
                        .needs
                        .iter()
                        .map(eos_types::TaskId::as_str)
                        .collect();
                    self.emit_task_event(
                        TASK_READY,
                        pending,
                        &[
                            ("status_from", json!("pending")),
                            ("status_to", json!("pending")),
                            ("satisfied_dependency_ids", json!(needs)),
                        ],
                    );
                }
                let task = deps
                    .task_store
                    .set_task_status_if_current(
                        &task_id,
                        TaskStatus::Pending,
                        TaskStatus::Running,
                        None,
                        None,
                    )
                    .await?;
                let Some(task) = task else {
                    continue;
                };
                self.emit_task_event(
                    TASK_LAUNCHED,
                    &task,
                    &[
                        ("status_from", json!("pending")),
                        ("status_to", json!("running")),
                    ],
                );
                let launch = match self.build_launch(&deps, &attempt, &task).await {
                    Ok(launch) => launch,
                    Err(err) => {
                        self.mark_launch_failed(&task, &err.to_string()).await?;
                        continue;
                    }
                };
                let runner = deps.runner.clone();
                set.spawn(async move {
                    let result = runner.run(launch.clone()).await;
                    (launch, result)
                });
            }

            let refreshed = self.orchestrator.fresh_attempt().await?;
            let refreshed_tasks = self.orchestrator.plan_task_records(&refreshed).await?;
            match dag_resolution(&refreshed_tasks)? {
                DagResolution::FailedOrBlocked => {
                    return self
                        .orchestrator
                        .close_attempt_failed(eos_types::AttemptFailReason::TaskFailed)
                        .await;
                }
                DagResolution::Passed => return self.orchestrator.close_attempt_passed().await,
                DagResolution::Running => {}
            }
            if set.is_empty() {
                return Ok(());
            }
            // The set is non-empty (guarded above), so `join_next` yields a
            // finished run; settle it and loop. The JoinSet aborts any still-in-
            // flight runs when it drops on return.
            if let Some(joined) = set.join_next().await {
                let (launch, report) =
                    joined.map_err(|err| WorkflowError::Join(err.to_string()))?;
                self.settle_run_task(launch, report).await?;
            }
        }
    }

    async fn build_launch(
        &self,
        deps: &AttemptResources,
        attempt: &Attempt,
        task: &Task,
    ) -> Result<AgentLaunch> {
        if task.role == TaskRole::Reducer {
            AgentLaunchFactory::new(deps.clone())
                .for_reducer(attempt, task)
                .await
        } else {
            let agent_name = task.agent_name.clone().ok_or_else(|| {
                WorkflowError::invariant(format!(
                    "task {:?} has no persisted agent profile",
                    task.id.as_str()
                ))
            })?;
            AgentLaunchFactory::new(deps.clone())
                .for_generator(attempt, task, &agent_name)
                .await
        }
    }

    async fn mark_launch_failed(&self, task: &Task, summary: &str) -> Result<()> {
        if task.attempt_id.is_none() {
            return Err(WorkflowError::invariant(format!(
                "task {:?} launch failure requires task.attempt_id",
                task.id.as_str()
            )));
        }
        let outcome = format!("agent launch failed: {summary}");
        let terminal_payload = json_object("fail_reason", "agent_launch_failed");
        let role = match task.role {
            TaskRole::Generator => ExecutionRole::Generator,
            TaskRole::Reducer => ExecutionRole::Reducer,
            _ => {
                return Err(WorkflowError::invariant(format!(
                    "task {:?} has unsupported launch-failure role {:?}",
                    task.id.as_str(),
                    task.role
                )))
            }
        };
        let result = execution_outcome_for_submission(
            task.id.clone(),
            role,
            TaskOutcomeStatus::Failed,
            outcome,
        );
        let outcomes = [result];
        let task = self
            .orchestrator
            .deps()
            .task_store
            .set_task_status_if_current(
                &task.id,
                TaskStatus::Running,
                TaskStatus::Failed,
                Some(&outcomes),
                Some(&terminal_payload),
            )
            .await?;
        let Some(task) = task else {
            return Ok(());
        };
        // D8: task_failed (running -> failed) on a launch failure.
        self.emit_task_event(
            TASK_FAILED,
            &task,
            &[
                ("status_from", json!("running")),
                ("status_to", json!("failed")),
                ("fail_reason", json!("agent_launch_failed")),
                ("summary", json!(summary)),
            ],
        );
        Ok(())
    }

    /// Emit one `workflow.task.*` human diagnostic row. These lifecycle shadows
    /// are reconstructable from task state, so runner correctness must come from
    /// state/transcript, not from this trace.
    fn emit_task_event(&self, event_type: &str, task: &Task, extra: &[(&str, Value)]) {
        let mut payload = JsonObject::new();
        payload.insert("request_id".to_owned(), json!(task.request_id.as_str()));
        payload.insert(
            "attempt_id".to_owned(),
            json!(task.attempt_id.as_ref().map(eos_types::AttemptId::as_str)),
        );
        payload.insert("task_id".to_owned(), json!(task.id.as_str()));
        payload.insert("role".to_owned(), json!(task_role_label(task.role)));
        payload.insert("agent_name".to_owned(), json!(task.agent_name.clone()));
        payload.insert(
            "needs".to_owned(),
            json!(task
                .needs
                .iter()
                .map(eos_types::TaskId::as_str)
                .collect::<Vec<_>>()),
        );
        for (key, value) in extra {
            payload.insert((*key).to_owned(), value.clone());
        }
        tracing::debug!(
            target: "eos_workflow::diagnostics",
            event_type,
            request_id = task.request_id.as_str(),
            task_id = task.id.as_str(),
            attempt_id = task
                .attempt_id
                .as_ref()
                .map(eos_types::AttemptId::as_str),
            role = task_role_label(task.role),
            agent_name = task.agent_name.as_deref(),
            payload = ?payload,
            "workflow task lifecycle"
        );
    }

    /// Settle a RUN-stage task after its run resolves (Path A-recording). The
    /// submit tool already recorded the agent's outcome (task Done/Failed) via
    /// the recording port *during* the run, so the loop's only post-join job is
    /// Rust's still-RUNNING exhaustion guard: a task still `Running` means the
    /// agent died without submitting -> synthesize `run_exhausted`. A recorded
    /// task is a no-op (the tool already wrote it).
    async fn settle_run_task(
        &self,
        launch: AgentLaunch,
        report: Result<AgentRunReport>,
    ) -> Result<()> {
        let task = self
            .orchestrator
            .deps()
            .task_store
            .get(launch.task_id())
            .await?;
        if matches!(task, Some(ref task) if task.status == TaskStatus::Running) {
            let summary = match report {
                Ok(report) => report
                    .failure_summary
                    .unwrap_or_else(|| "agent run ended without a terminal submission".to_owned()),
                Err(err) => format!("agent run failed: {err}"),
            };
            self.synthesize_failure(&launch, &summary).await
        } else {
            Ok(())
        }
    }

    async fn synthesize_failure(&self, launch: &AgentLaunch, summary: &str) -> Result<()> {
        let attempt_id = launch.attempt_id().clone();
        let exhausted = json_object("fail_reason", "run_exhausted");
        match launch.role() {
            TaskRole::Planner => {
                self.orchestrator
                    .apply_planner_failure(PlannerFailureSubmission {
                        attempt_id,
                        planner_task_id: launch.task_id().clone(),
                        fail_reason: PlannerFailReason::RunExhausted,
                    })
                    .await
            }
            TaskRole::Generator => {
                self.orchestrator
                    .record_generator_submission(GeneratorSubmission {
                        attempt_id,
                        task_id: launch.task_id().clone(),
                        status: TaskOutcomeStatus::Failed,
                        outcome: summary.to_owned(),
                        terminal_payload: exhausted,
                    })
                    .await
            }
            TaskRole::Reducer => {
                self.orchestrator
                    .record_reducer_submission(ReducerSubmission {
                        attempt_id,
                        task_id: launch.task_id().clone(),
                        status: TaskOutcomeStatus::Failed,
                        outcome: summary.to_owned(),
                        terminal_payload: exhausted,
                    })
                    .await
            }
            TaskRole::Root => Err(WorkflowError::invariant(format!(
                "no exhaustion reporter for role {:?}",
                launch.role()
            ))),
        }
    }
}

/// The lowercase role label used in `workflow.task.*` audit payloads (Rust
/// persists `task.role` as a lowercase string).
fn task_role_label(role: TaskRole) -> &'static str {
    role.as_str()
}

#[cfg(test)]
#[path = "../../tests/attempt/run_stage/mod.rs"]
mod tests;

// Plan-DAG materialization + PLAN->RUN, asserted at a non-closure park
// (TESTING_SPEC §9 `plan_dag`; the Layer-B half of AC6).
#[cfg(test)]
#[path = "../../tests/plan_dag/mod.rs"]
mod plan_dag_tests;
