//! The `check_subagent_progress` control tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{JsonObject, SubagentSessionId};
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};

use crate::config::ToolConfigSet;
use crate::error::ToolError;
use crate::execution::parse_input;
use crate::executor::ToolExecutor;
use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::registry::ToolRegistry;
use crate::result::{OutputShape, ToolResult};
use crate::spec::text_spec;

use super::lib::{default_five, empty_subagent_session_error};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct CheckSubagentProgressInput {
    subagent_session_id: SubagentSessionId,
    // Keep schema and runtime validation aligned.
    #[serde(default = "default_five")]
    #[schemars(default = "default_five", range(min = 1, max = 10))]
    last_n_messages: u8,
}

pub(in crate::model_tools::subagent) struct CheckSubagentProgress;

#[async_trait]
impl ToolExecutor for CheckSubagentProgress {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: CheckSubagentProgressInput =
            match parse_input(ToolName::CheckSubagentProgress, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        if parsed.subagent_session_id.as_str().is_empty() {
            return Ok(empty_subagent_session_error(
                ToolName::CheckSubagentProgress,
            ));
        }
        if !(1..=10).contains(&parsed.last_n_messages) {
            return Ok(ToolResult::error(
                "Invalid input for check_subagent_progress: last_n_messages must be between 1 and 10. \
                 Please retry the tool call with valid arguments.",
            ));
        }
        ctx.require_background_supervisor()?
            .progress(&parsed.subagent_session_id, parsed.last_n_messages)
            .await
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let check = config.get(ToolName::CheckSubagentProgress);
    super::super::register_tool(
        registry,
        ToolName::CheckSubagentProgress,
        check,
        text_spec(
            ToolName::CheckSubagentProgress,
            &check.description,
            schema_for!(CheckSubagentProgressInput),
        ),
        OutputShape::Text,
        Arc::new(CheckSubagentProgress),
    );
}
