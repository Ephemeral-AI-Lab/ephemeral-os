//! The `submit_worker_outcome` terminal tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{JsonObject, WorkerOutcomeSubmission};
use schemars::schema_for;
use serde_json::json;

use crate::registry::{text_spec, ToolConfigSet};
use crate::tools::{parse_input, AttemptSubmissionHandle};
use crate::{
    ExecutionMetadata, OutputShape, ToolError, ToolExecutor, ToolName, ToolRegistry, ToolResult,
};

use super::support::{is_blank, meta_obj, submission_ack_result, OutcomeInput};

struct SubmitWorkerOutcome {
    service: AttemptSubmissionHandle,
}

impl SubmitWorkerOutcome {
    fn new(service: AttemptSubmissionHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for SubmitWorkerOutcome {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: OutcomeInput = match parse_input(ToolName::SubmitWorkerOutcome, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if is_blank(&parsed.outcome) {
            return Ok(ToolResult::error("outcome must be nonblank"));
        }
        let agent_run_id = ctx.require_agent_run_id()?.clone();
        let is_pass = parsed.status.is_pass();
        let outcome = parsed.outcome.clone();
        let submission = WorkerOutcomeSubmission {
            agent_run_id: agent_run_id.clone(),
            status: parsed.status,
            outcome: parsed.outcome,
        };
        let ack = self.service.api.submit_worker_outcome(submission).await?;
        Ok(submission_ack_result(
            ack,
            &format!("Accepted worker {}.", parsed.status.as_str()),
            &meta_obj(&[
                ("kind", json!("worker")),
                ("is_pass", json!(is_pass)),
                ("outcome", json!(outcome)),
                ("agent_run_id", json!(agent_run_id.as_str())),
            ]),
        ))
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    attempt_submission: AttemptSubmissionHandle,
) {
    let worker = config.get(ToolName::SubmitWorkerOutcome);
    crate::tools::register_tool(
        registry,
        ToolName::SubmitWorkerOutcome,
        worker,
        text_spec(
            ToolName::SubmitWorkerOutcome,
            &worker.description,
            schema_for!(OutcomeInput),
        ),
        OutputShape::Text,
        Arc::new(SubmitWorkerOutcome::new(attempt_submission)),
    );
}

pub(super) fn register_schema(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let worker = config.get(ToolName::SubmitWorkerOutcome);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitWorkerOutcome,
        worker,
        text_spec(
            ToolName::SubmitWorkerOutcome,
            &worker.description,
            schema_for!(OutcomeInput),
        ),
        OutputShape::Text,
    );
}
