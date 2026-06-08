//! The `submit_subagent_result` terminal.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::registry::config::ToolConfigSet;
use crate::registry::spec::text_spec;
use crate::runtime::execution::parse_input;
use eos_tool_ports::ExecutionMetadata;
use eos_tool_ports::ToolError;
use eos_tool_ports::ToolExecutor;
use eos_tool_ports::ToolName;
use eos_tool_ports::ToolRegistry;
use eos_tool_ports::{OutputShape, ToolResult};

use super::super::lib::{is_blank, meta_obj};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct SubmitSubagentResultInput {
    summary: String,
    #[serde(default)]
    findings: Vec<String>,
    #[serde(default)]
    references: Vec<String>,
}

struct SubmitSubagentResult;

#[async_trait]
impl ToolExecutor for SubmitSubagentResult {
    async fn execute(
        &self,
        input: &JsonObject,
        _ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: SubmitSubagentResultInput =
            match parse_input(ToolName::SubmitSubagentResult, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        if is_blank(&parsed.summary) {
            return Ok(ToolResult::error("summary must be nonblank"));
        }
        Ok(ToolResult::ok(parsed.summary).with_metadata(meta_obj(&[
            ("agent_type", json!("subagent")),
            ("findings", json!(parsed.findings)),
            ("references", json!(parsed.references)),
        ])))
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let subagent = config.get(ToolName::SubmitSubagentResult);
    super::super::super::register_tool(
        registry,
        ToolName::SubmitSubagentResult,
        subagent,
        text_spec(
            ToolName::SubmitSubagentResult,
            &subagent.description,
            schema_for!(SubmitSubagentResultInput),
        ),
        OutputShape::Text,
        Arc::new(SubmitSubagentResult),
    );
}
