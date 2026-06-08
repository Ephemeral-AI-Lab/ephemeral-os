use std::sync::Arc;

use async_trait::async_trait;
use eos_types::GeneratorSubmission;
use eos_types::JsonObject;
use schemars::schema_for;
use serde_json::json;

use crate::registry::config::ToolConfigSet;
use crate::registry::spec::text_spec;
use crate::runtime::execution::parse_input;
use crate::tools::AttemptSubmissionService;
use eos_tool_ports::ExecutionMetadata;
use eos_tool_ports::ToolError;
use eos_tool_ports::ToolExecutor;
use eos_tool_ports::ToolName;
use eos_tool_ports::ToolRegistry;
use eos_tool_ports::{OutputShape, ToolResult};

use super::super::lib::{
    is_blank, meta_obj, submission_ack_result, OutcomeInput, SubmissionStatus,
};

struct SubmitGeneratorOutcome {
    service: Option<AttemptSubmissionService>,
}

impl SubmitGeneratorOutcome {
    fn new(service: Option<AttemptSubmissionService>) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for SubmitGeneratorOutcome {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: OutcomeInput = match parse_input(ToolName::SubmitGeneratorOutcome, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if is_blank(&parsed.outcome) {
            return Ok(ToolResult::error("outcome must be nonblank"));
        }
        let attempt_id = ctx.require_attempt_id()?.clone();
        let task_id = ctx.require_task_id()?.clone();
        let submission = GeneratorSubmission {
            attempt_id,
            task_id: task_id.clone(),
            status: parsed.status.outcome_status(),
            outcome: parsed.outcome.clone(),
            terminal_tool_result: meta_obj(&[("generator_role", json!("generator"))]),
        };
        let ack = self
            .service
            .as_ref()
            .ok_or(ToolError::MissingPort("attempt_submission"))?
            .port
            .submit_generator(submission)
            .await?;
        Ok(submission_ack_result(
            ack,
            &format!("Accepted generator {}.", parsed.status.as_str()),
            &meta_obj(&[
                (
                    "submission_kind",
                    json!(if parsed.status == SubmissionStatus::Success {
                        "generator_success"
                    } else {
                        "generator_failure"
                    }),
                ),
                ("task_id", json!(task_id.as_str())),
                (
                    "attempt_id",
                    json!(ctx.attempt_id.as_ref().map(eos_types::AttemptId::as_str)),
                ),
            ]),
        ))
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    attempt_submission: Option<AttemptSubmissionService>,
) {
    let generator = config.get(ToolName::SubmitGeneratorOutcome);
    super::super::super::register_tool(
        registry,
        ToolName::SubmitGeneratorOutcome,
        generator,
        text_spec(
            ToolName::SubmitGeneratorOutcome,
            &generator.description,
            schema_for!(OutcomeInput),
        ),
        OutputShape::Text,
        Arc::new(SubmitGeneratorOutcome::new(attempt_submission)),
    );
}
