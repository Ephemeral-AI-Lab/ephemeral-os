use std::collections::BTreeMap;
use std::sync::Arc;

use eos_types::{
    AgentRunId, Attempt, AttemptFailReason, AttemptStage, ExecutionNode, ExecutionStatus,
    SubmissionOutcome, WorkItemId, WorkItemSpec, WorkerOutcomeSubmission,
};

use crate::{Result, WorkflowError};

use super::work_items::{planner_outcome_for_attempt, work_item_by_id};
use super::{AgentLaunch, AgentLaunchFactory, AgentRunReport, AttemptRun};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NodeState {
    Missing,
    Running,
    Passed,
    Failed,
}

/// Worker wave execution and settlement for one attempt.
pub(crate) struct WorkItemsRun {
    attempt_run: Arc<AttemptRun>,
}

impl WorkItemsRun {
    pub(crate) fn new(attempt_run: Arc<AttemptRun>) -> Self {
        Self { attempt_run }
    }

    pub(crate) async fn advance(&self) -> Result<()> {
        loop {
            let attempt = self.attempt_run.fresh_attempt().await?;
            if attempt.is_closed() {
                return Ok(());
            }
            if attempt.stage() != AttemptStage::Run {
                return Ok(());
            }
            let planner = planner_outcome_for_attempt(self.attempt_run.deps(), &attempt).await?;
            let states = self.node_states(&attempt)?;
            if states.values().any(|state| *state == NodeState::Failed) {
                return self
                    .attempt_run
                    .close_attempt_failed(AttemptFailReason::AgentRunFailed)
                    .await;
            }
            if !states.is_empty() && states.values().all(|state| *state == NodeState::Passed) {
                return self.attempt_run.close_attempt_passed().await;
            }

            let running_count = states
                .values()
                .filter(|state| **state == NodeState::Running)
                .count();
            let capacity = self
                .attempt_run
                .deps()
                .max_concurrent_worker_runs
                .saturating_sub(running_count);
            let ready = self.ready_unbound_nodes(&attempt, &states);
            if capacity == 0 || ready.is_empty() {
                if running_count == 0 && self.unbound_nodes_are_blocked(&attempt, &states) {
                    return self
                        .attempt_run
                        .close_attempt_failed(AttemptFailReason::AgentRunFailed)
                        .await;
                }
                return Ok(());
            }
            let mut spawned = 0usize;
            for node in ready.into_iter().take(capacity) {
                let work_item = work_item_by_id(&planner.work_items, &node.work_item_id)?.clone();
                self.spawn_worker(&attempt, &work_item).await?;
                spawned += 1;
            }
            if spawned == 0 {
                return Ok(());
            }
        }
    }

    pub(crate) async fn record_worker_outcome(
        &self,
        submission: WorkerOutcomeSubmission,
    ) -> Result<()> {
        let attempt = self.attempt_run.assert_stage(AttemptStage::Run).await?;
        let node = node_for_agent_run(&attempt, &submission.agent_run_id)?;
        let work_item_id = node.work_item_id.clone();
        if node.status != Some(ExecutionStatus::Running) {
            return Err(WorkflowError::invariant(format!(
                "worker agent run {:?} is not running",
                submission.agent_run_id.as_str()
            )));
        }

        let status = if submission.status.is_pass() {
            ExecutionStatus::Done
        } else {
            ExecutionStatus::Failed
        };
        let outcome = SubmissionOutcome::Worker {
            is_pass: submission.status.is_pass(),
            outcome: submission.outcome,
        };
        self.attempt_run
            .deps()
            .attempt_store
            .record_worker_outcome(&attempt.id, &work_item_id, status, &outcome)
            .await?;
        self.advance().await
    }

    async fn spawn_worker(&self, attempt: &Attempt, work_item: &WorkItemSpec) -> Result<()> {
        let agent_run_id = AgentRunId::new_v4();
        let launch = AgentLaunchFactory::new(self.attempt_run.deps().clone())
            .for_worker(attempt, work_item, agent_run_id.clone())
            .await?;
        self.attempt_run
            .deps()
            .attempt_store
            .bind_worker_agent_run(&attempt.id, &work_item.id, &agent_run_id)
            .await?;
        self.attempt_run
            .deps()
            .active_attempt_runs
            .register_agent_run(agent_run_id, Arc::clone(&self.attempt_run))?;
        self.spawn_worker_run(launch);
        Ok(())
    }

    fn spawn_worker_run(&self, launch: AgentLaunch) {
        let attempt_run = Arc::clone(&self.attempt_run);
        let runner = self.attempt_run.deps().runner.clone();
        tokio::spawn(async move {
            let report = runner.run(launch.clone()).await;
            if let Err(err) = WorkItemsRun::new(attempt_run)
                .settle_worker(launch, report)
                .await
            {
                tracing::warn!(error = %err, "worker run could not be settled");
            }
        });
    }

    async fn settle_worker(
        &self,
        launch: AgentLaunch,
        report: Result<AgentRunReport>,
    ) -> Result<()> {
        let summary = match report {
            Ok(report) => report.failure_summary,
            Err(err) => Some(err.to_string()),
        };
        if let Some(summary) = summary {
            tracing::warn!(
                attempt_id = %self.attempt_run.attempt_id().as_str(),
                agent_run_id = %launch.agent_run_id.as_str(),
                %summary,
                "worker run reported a failure summary"
            );
        }
        let attempt = self.attempt_run.fresh_attempt().await?;
        if attempt.is_closed() {
            return Ok(());
        }
        let node = node_for_agent_run(&attempt, &launch.agent_run_id)?;
        let work_item_id = node.work_item_id.clone();
        if matches!(
            node.status,
            None | Some(ExecutionStatus::Pending | ExecutionStatus::Running)
        ) {
            let outcome = SubmissionOutcome::Worker {
                is_pass: false,
                outcome: "worker finished without submit_worker_outcome".to_owned(),
            };
            self.attempt_run
                .deps()
                .attempt_store
                .record_worker_outcome(
                    &attempt.id,
                    &work_item_id,
                    ExecutionStatus::Failed,
                    &outcome,
                )
                .await?;
        }
        self.advance().await
    }

    fn node_states(&self, attempt: &Attempt) -> Result<BTreeMap<WorkItemId, NodeState>> {
        let mut states = BTreeMap::new();
        for node in &attempt.execution_tree.nodes {
            states.insert(node.work_item_id.clone(), node_state(node)?);
        }
        Ok(states)
    }

    fn ready_unbound_nodes<'a>(
        &self,
        attempt: &'a Attempt,
        states: &BTreeMap<WorkItemId, NodeState>,
    ) -> Vec<&'a ExecutionNode> {
        attempt
            .execution_tree
            .nodes
            .iter()
            .filter(|node| {
                node.agent_run_id.is_none()
                    && node
                        .needs
                        .iter()
                        .all(|need| states.get(need) == Some(&NodeState::Passed))
            })
            .collect()
    }

    fn unbound_nodes_are_blocked(
        &self,
        attempt: &Attempt,
        states: &BTreeMap<WorkItemId, NodeState>,
    ) -> bool {
        attempt
            .execution_tree
            .nodes
            .iter()
            .any(|node| node.agent_run_id.is_none())
            && !attempt.execution_tree.nodes.iter().any(|node| {
                node.agent_run_id.is_none()
                    && node
                        .needs
                        .iter()
                        .all(|need| states.get(need) == Some(&NodeState::Passed))
            })
    }
}

fn node_state(node: &ExecutionNode) -> Result<NodeState> {
    match node.status {
        None => Ok(NodeState::Missing),
        Some(ExecutionStatus::Pending | ExecutionStatus::Running) => Ok(NodeState::Running),
        Some(ExecutionStatus::Done) => match &node.outcome {
            Some(SubmissionOutcome::Worker { is_pass: true, .. }) => Ok(NodeState::Passed),
            Some(SubmissionOutcome::Worker { .. }) => Ok(NodeState::Failed),
            Some(_) | None => Err(WorkflowError::invariant(format!(
                "work item {:?} is done without worker outcome",
                node.work_item_id.as_str()
            ))),
        },
        Some(ExecutionStatus::Failed | ExecutionStatus::Blocked | ExecutionStatus::Cancelled) => {
            Ok(NodeState::Failed)
        }
    }
}

fn node_for_agent_run<'a>(
    attempt: &'a Attempt,
    agent_run_id: &AgentRunId,
) -> Result<&'a ExecutionNode> {
    attempt
        .execution_tree
        .nodes
        .iter()
        .find(|node| node.agent_run_id.as_ref() == Some(agent_run_id))
        .ok_or_else(|| WorkflowError::not_found("worker agent run", agent_run_id.as_str()))
}
