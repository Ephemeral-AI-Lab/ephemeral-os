use std::sync::Arc;

use eos_types::{
    AgentRunId, AttemptFailReason, AttemptStage, PlanOutcomeSubmission, SubmissionOutcome,
};

use crate::{Result, WorkflowError};

use super::work_items::{execution_nodes, validate_work_items};
use super::work_items_run::WorkItemsRun;
use super::{AgentLaunch, AgentLaunchFactory, AgentRunReport, AttemptRun};

/// Planner launch and terminal-plan settlement for one attempt.
pub(crate) struct PlannerRun {
    attempt_run: Arc<AttemptRun>,
}

impl PlannerRun {
    pub(crate) fn new(attempt_run: Arc<AttemptRun>) -> Self {
        Self { attempt_run }
    }

    pub(crate) async fn start(&self) -> Result<()> {
        self.attempt_run.validate_run_concurrency()?;
        let attempt = self.attempt_run.assert_stage(AttemptStage::Plan).await?;
        if attempt.planner_started() {
            return Err(WorkflowError::invariant(format!(
                "attempt {:?} already started its planner",
                attempt.id.as_str()
            )));
        }

        let agent_run_id = AgentRunId::new_v4();
        let launch = AgentLaunchFactory::new(self.attempt_run.deps().clone())
            .for_planner(&attempt, agent_run_id.clone())
            .await?;
        self.attempt_run
            .deps()
            .attempt_store
            .mark_planner_started(&attempt.id)
            .await?;
        self.attempt_run
            .deps()
            .active_attempt_runs
            .register(Arc::clone(&self.attempt_run))?;
        self.attempt_run
            .deps()
            .active_attempt_runs
            .register_agent_run(agent_run_id, Arc::clone(&self.attempt_run))?;
        self.spawn_planner_run(launch);
        Ok(())
    }

    pub(crate) async fn record_plan_outcome(
        &self,
        submission: PlanOutcomeSubmission,
    ) -> Result<()> {
        self.attempt_run.validate_run_concurrency()?;
        let attempt = self.attempt_run.assert_stage(AttemptStage::Plan).await?;
        if !attempt.planner_started() {
            return Err(WorkflowError::invariant(format!(
                "attempt {:?} has not started its planner",
                attempt.id.as_str()
            )));
        }
        validate_work_items(
            &submission.work_items,
            &self.attempt_run.deps().agent_registry,
        )?;

        let outcome = SubmissionOutcome::Planner {
            plan_spec: submission.plan_spec,
            work_items: submission.work_items.clone(),
            deferred_goal_for_next_iteration: submission.deferred_goal_for_next_iteration,
        };
        let nodes = execution_nodes(&submission.work_items);
        self.attempt_run
            .deps()
            .attempt_store
            .record_plan_outcome(&attempt.id, &outcome, &nodes)
            .await?;
        WorkItemsRun::new(Arc::clone(&self.attempt_run))
            .advance()
            .await
    }

    fn spawn_planner_run(&self, launch: AgentLaunch) {
        let attempt_run = Arc::clone(&self.attempt_run);
        let runner = self.attempt_run.deps().runner.clone();
        tokio::spawn(async move {
            let report = runner.run(launch.clone()).await;
            if let Err(err) = PlannerRun::new(attempt_run)
                .settle_planner(launch, report)
                .await
            {
                tracing::warn!(error = %err, "planner run could not be settled");
            }
        });
    }

    async fn settle_planner(
        &self,
        launch: AgentLaunch,
        report: Result<AgentRunReport>,
    ) -> Result<()> {
        let failed = match report {
            Ok(report) => report.failure_summary,
            Err(err) => Some(err.to_string()),
        };
        if let Some(summary) = failed {
            tracing::warn!(
                attempt_id = %self.attempt_run.attempt_id().as_str(),
                agent_run_id = %launch.agent_run_id.as_str(),
                %summary,
                "planner run reported a failure summary"
            );
        }

        let attempt = self.attempt_run.fresh_attempt().await?;
        if attempt.is_closed() {
            return Ok(());
        }
        if attempt.execution_tree.planner_outcome.is_some() {
            return WorkItemsRun::new(Arc::clone(&self.attempt_run))
                .advance()
                .await;
        }

        self.attempt_run
            .close_attempt_failed(AttemptFailReason::AgentRunFailed)
            .await
    }
}
