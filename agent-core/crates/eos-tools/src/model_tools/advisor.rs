//! The `ask_advisor` helper tool — a blocking read-only advisor audit of a
//! pending terminal submission. Calls the [`AdvisorPort`].

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};

use crate::error::ToolError;
use crate::execution::parse_input;
use crate::executor::ToolExecutor;
use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::registry::ToolRegistry;
use crate::result::{OutputShape, ToolResult};
use crate::spec::text_spec;

const ASK_ADVISOR_DESCRIPTION: &str = include_str!("descriptions/ask_advisor.md");

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct AskAdvisorInput {
    /// The terminal tool the caller intends to call.
    tool_name: String,
    /// The arguments the caller intends to pass.
    #[serde(default)]
    tool_payload: JsonObject,
}

struct AskAdvisor;

#[async_trait]
impl ToolExecutor for AskAdvisor {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: AskAdvisorInput = match parse_input(ToolName::AskAdvisor, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.tool_name.trim().is_empty() {
            return Ok(ToolResult::error("tool_name must be nonblank"));
        }
        let output = ctx
            .require_advisor()?
            .review(&parsed.tool_name, &parsed.tool_payload)
            .await?;
        Ok(ToolResult::ok(output))
    }
}

pub(crate) fn register(registry: &mut ToolRegistry) {
    super::register_tool(
        registry,
        ToolName::AskAdvisor,
        text_spec(
            ToolName::AskAdvisor,
            ASK_ADVISOR_DESCRIPTION,
            schema_for!(AskAdvisorInput),
        ),
        OutputShape::Text,
        Arc::new(AskAdvisor),
    );
}
