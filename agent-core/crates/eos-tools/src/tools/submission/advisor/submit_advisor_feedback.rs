//! The `submit_advisor_feedback` helper terminal.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
use crate::core::result::{OutputShape, ToolResult};
use crate::registry::config::ToolConfigSet;
use crate::registry::spec::text_spec;
use crate::registry::ToolRegistry;
use crate::runtime::execution::parse_input;
use crate::runtime::executor::ToolExecutor;

use super::super::lib::{is_blank, meta_obj};

/// `Literal["approve", "reject"]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
enum Verdict {
    Approve,
    Reject,
}

impl Verdict {
    fn as_str(self) -> &'static str {
        match self {
            Verdict::Approve => "approve",
            Verdict::Reject => "reject",
        }
    }
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct SubmitAdvisorFeedbackInput {
    verdict: Verdict,
    summary: String,
}

struct SubmitAdvisorFeedback;

#[async_trait]
impl ToolExecutor for SubmitAdvisorFeedback {
    async fn execute(
        &self,
        input: &JsonObject,
        _ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: SubmitAdvisorFeedbackInput =
            match parse_input(ToolName::SubmitAdvisorFeedback, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        if is_blank(&parsed.summary) {
            return Ok(ToolResult::error("summary must be nonblank"));
        }
        Ok(ToolResult::ok(parsed.summary).with_metadata(meta_obj(&[
            ("helper_role", json!("advisor")),
            ("verdict", json!(parsed.verdict.as_str())),
        ])))
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let advisor = config.get(ToolName::SubmitAdvisorFeedback);
    super::super::super::register_tool(
        registry,
        ToolName::SubmitAdvisorFeedback,
        advisor,
        text_spec(
            ToolName::SubmitAdvisorFeedback,
            &advisor.description,
            schema_for!(SubmitAdvisorFeedbackInput),
        ),
        OutputShape::Text,
        Arc::new(SubmitAdvisorFeedback),
    );
}
