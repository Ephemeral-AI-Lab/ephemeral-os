//! The `check_subagent_progress` control tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{JsonObject, SubagentSessionId};
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
use crate::core::result::{OutputShape, ToolResult};
use crate::ports::{SubagentProgress, SubagentSessionPort};
use crate::registry::config::ToolConfigSet;
use crate::registry::spec::text_spec;
use crate::registry::ToolRegistry;
use crate::runtime::execution::parse_input;
use crate::runtime::executor::ToolExecutor;

use super::lib::{default_five, empty_subagent_session_error, subagent_status_and_result};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct CheckSubagentProgressInput {
    subagent_session_id: SubagentSessionId,
    // Keep schema and runtime validation aligned.
    #[serde(default = "default_five")]
    #[schemars(default = "default_five", range(min = 1, max = 10))]
    last_n_messages: u8,
}

pub(in crate::tools::subagent) struct CheckSubagentProgress {
    subagent_sessions: Option<Arc<dyn SubagentSessionPort>>,
}

impl CheckSubagentProgress {
    pub(in crate::tools::subagent) fn new(
        subagent_sessions: Option<Arc<dyn SubagentSessionPort>>,
    ) -> Self {
        Self { subagent_sessions }
    }
}

#[async_trait]
impl ToolExecutor for CheckSubagentProgress {
    async fn execute(
        &self,
        input: &JsonObject,
        _ctx: &ExecutionMetadata,
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
        match self
            .subagent_sessions
            .as_deref()
            .ok_or(ToolError::MissingPort("subagent_sessions"))?
            .subagent_session_snapshot(&parsed.subagent_session_id)
            .await
            .unwrap_or(SubagentProgress::Missing {
                subagent_session_id: parsed.subagent_session_id.clone(),
            }) {
            SubagentProgress::Found {
                subagent_session_id,
                status,
                agent_name,
                result,
            } => Ok(render_progress(
                &subagent_session_id,
                status,
                agent_name.as_str(),
                result.as_ref(),
            )),
            SubagentProgress::Missing {
                subagent_session_id,
            } => Ok(ToolResult::error(format!(
                "No subagent session found with ID: {}",
                subagent_session_id.as_str()
            ))),
        }
    }
}

fn render_progress(
    subagent_session_id: &SubagentSessionId,
    status: crate::ports::SubagentSessionStatus,
    agent_name: &str,
    result: Option<&ToolResult>,
) -> ToolResult {
    let (status, result_text) = subagent_status_and_result(status, result);
    let payload = json!({
        "subagent_session_id": subagent_session_id.as_str(),
        "status": status,
        "agent_name": agent_name,
        "result": result_text,
    });
    let output = serde_json::to_string_pretty(&payload).unwrap_or_else(|_| payload.to_string());
    let mut metadata = JsonObject::new();
    metadata.insert("subagent_snapshot".to_owned(), payload);
    ToolResult {
        output,
        is_error: false,
        metadata,
        is_terminal: false,
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    subagent_sessions: Option<Arc<dyn SubagentSessionPort>>,
) {
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
        Arc::new(CheckSubagentProgress::new(subagent_sessions)),
    );
}
