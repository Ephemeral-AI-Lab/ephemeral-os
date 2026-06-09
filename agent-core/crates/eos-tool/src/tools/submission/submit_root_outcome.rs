//! The `submit_root_submission_outcome` terminal tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{JsonObject, RequestStatus};
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::registry::{text_spec, ToolConfigSet};
use crate::tools::{parse_input, RootSubmissionHandle};
use crate::{
    ExecutionMetadata, OutputShape, ToolError, ToolExecutor, ToolName, ToolRegistry, ToolResult,
};

use super::support::{is_blank, meta_obj};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct SubmitRootSubmissionOutcomeInput {
    status: eos_types::SubmissionStatus,
    outcome: String,
}

struct SubmitRootSubmissionOutcome {
    service: RootSubmissionHandle,
}

impl SubmitRootSubmissionOutcome {
    fn new(service: RootSubmissionHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for SubmitRootSubmissionOutcome {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: SubmitRootSubmissionOutcomeInput =
            match parse_input(ToolName::SubmitRootSubmissionOutcome, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        if is_blank(&parsed.outcome) {
            return Ok(ToolResult::error("outcome must be nonblank"));
        }

        let request_id = ctx.require_request_id()?;
        let agent_run_id = ctx.require_agent_run_id()?;
        let request_store = self.service.submission.request_store()?;
        let request_status = if parsed.status.is_pass() {
            RequestStatus::Done
        } else {
            RequestStatus::Failed
        };
        request_store
            .finish_request(request_id, request_status)
            .await?;

        Ok(
            ToolResult::ok(format!("Accepted root {}.", parsed.status.as_str())).with_metadata(
                meta_obj(&[
                    ("kind", json!("root")),
                    ("is_pass", json!(parsed.status.is_pass())),
                    ("outcome", json!(parsed.outcome)),
                    ("request_id", json!(request_id.as_str())),
                    ("agent_run_id", json!(agent_run_id.as_str())),
                ]),
            ),
        )
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    root_submission: RootSubmissionHandle,
) {
    let root = config.get(ToolName::SubmitRootSubmissionOutcome);
    crate::tools::register_tool(
        registry,
        ToolName::SubmitRootSubmissionOutcome,
        root,
        text_spec(
            ToolName::SubmitRootSubmissionOutcome,
            &root.description,
            schema_for!(SubmitRootSubmissionOutcomeInput),
        ),
        OutputShape::Text,
        Arc::new(SubmitRootSubmissionOutcome::new(root_submission)),
    );
}

pub(super) fn register_schema(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let root = config.get(ToolName::SubmitRootSubmissionOutcome);
    crate::tools::register_schema_tool(
        registry,
        ToolName::SubmitRootSubmissionOutcome,
        root,
        text_spec(
            ToolName::SubmitRootSubmissionOutcome,
            &root.description,
            schema_for!(SubmitRootSubmissionOutcomeInput),
        ),
        OutputShape::Text,
    );
}
