//! Recording adapter from the `eos-tool` planner/generator/reducer terminal
//! submission port to the active per-attempt orchestrators.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{
    AttemptSubmissionPort, CoreError, GeneratorSubmission, PlannerPlan, ReducerSubmission,
    SubmissionAck,
};

use crate::attempt::AttemptOrchestratorRegistry;
use crate::WorkflowError;

/// Recording adapter from the `eos-tool` planner/generator/reducer terminal
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

#[async_trait]
impl AttemptSubmissionPort for AttemptSubmissionAdapter {
    async fn apply_plan(&self, plan: PlannerPlan) -> Result<SubmissionAck, CoreError> {
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
    ) -> Result<SubmissionAck, CoreError> {
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
    ) -> Result<SubmissionAck, CoreError> {
        let Some(orchestrator) = self.registry.get(&submission.attempt_id) else {
            return Ok(SubmissionAck::Rejected(format!(
                "attempt {:?} is not active",
                submission.attempt_id.as_str()
            )));
        };
        submission_ack(orchestrator.record_reducer_submission(submission).await)
    }
}

fn submission_ack(result: crate::Result<()>) -> Result<SubmissionAck, CoreError> {
    match result {
        Ok(()) => Ok(SubmissionAck::Accepted),
        Err(WorkflowError::Store(err)) => Err(err),
        Err(err) => Ok(SubmissionAck::Rejected(err.to_string())),
    }
}
