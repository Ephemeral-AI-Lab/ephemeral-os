//! The `submit_exploration_result` helper terminal.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::config::ToolConfigSet;
use crate::error::ToolError;
use crate::execution::parse_input;
use crate::executor::ToolExecutor;
use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::registry::ToolRegistry;
use crate::result::{OutputShape, ToolResult};
use crate::spec::text_spec;

use super::super::lib::{is_blank, meta_obj};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct SubmitExplorationResultInput {
    summary: String,
    #[serde(default)]
    findings: Vec<String>,
    #[serde(default)]
    references: Vec<String>,
}

struct SubmitExplorationResult;

#[async_trait]
impl ToolExecutor for SubmitExplorationResult {
    async fn execute(
        &self,
        input: &JsonObject,
        _ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: SubmitExplorationResultInput =
            match parse_input(ToolName::SubmitExplorationResult, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        if is_blank(&parsed.summary) {
            return Ok(ToolResult::error("summary must be nonblank"));
        }
        Ok(ToolResult::ok(parsed.summary).with_metadata(meta_obj(&[
            ("subagent_role", json!("explorer")),
            ("findings", json!(parsed.findings)),
            ("references", json!(parsed.references)),
        ])))
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let exploration = config.get(ToolName::SubmitExplorationResult);
    super::super::super::register_tool(
        registry,
        ToolName::SubmitExplorationResult,
        exploration,
        text_spec(
            ToolName::SubmitExplorationResult,
            &exploration.description,
            schema_for!(SubmitExplorationResultInput),
        ),
        OutputShape::Text,
        Arc::new(SubmitExplorationResult),
    );
}
