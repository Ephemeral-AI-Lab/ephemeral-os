//! The `cancel_subagent` control tool.

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

use super::lib::empty_subagent_session_error;

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct CancelSubagentInput {
    subagent_session_id: SubagentSessionId,
    #[serde(default)]
    reason: String,
}

pub(in crate::model_tools::subagent) struct CancelSubagent;

#[async_trait]
impl ToolExecutor for CancelSubagent {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: CancelSubagentInput = match parse_input(ToolName::CancelSubagent, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.subagent_session_id.as_str().is_empty() {
            return Ok(empty_subagent_session_error(ToolName::CancelSubagent));
        }
        ctx.require_background_supervisor()?
            .cancel(&parsed.subagent_session_id, &parsed.reason)
            .await
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let cancel = config.get(ToolName::CancelSubagent);
    super::super::register_tool(
        registry,
        ToolName::CancelSubagent,
        cancel,
        text_spec(
            ToolName::CancelSubagent,
            &cancel.description,
            schema_for!(CancelSubagentInput),
        ),
        OutputShape::Text,
        Arc::new(CancelSubagent),
    );
}
