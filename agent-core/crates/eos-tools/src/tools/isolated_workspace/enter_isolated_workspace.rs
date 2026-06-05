//! The `enter_isolated_workspace` lifecycle tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
use crate::core::result::{OutputShape, ToolResult};
use crate::registry::config::ToolConfigSet;
use crate::registry::spec::text_spec;
use crate::registry::ToolRegistry;
use crate::runtime::execution::parse_input;
use crate::runtime::executor::ToolExecutor;

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct EnterIsolatedWorkspaceInput {
    #[serde(default)]
    layer_stack_root: String,
}

struct EnterIsolatedWorkspace;

#[async_trait]
impl ToolExecutor for EnterIsolatedWorkspace {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: EnterIsolatedWorkspaceInput =
            match parse_input(ToolName::EnterIsolatedWorkspace, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
        };
        let sandbox_id = ctx.require_sandbox_id()?;
        let agent_run_id = ctx.require_agent_run_id()?;
        ctx.require_isolated_workspace()?
            .enter(agent_run_id, sandbox_id, &parsed.layer_stack_root)
            .await
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let enter = config.get(ToolName::EnterIsolatedWorkspace);
    super::super::register_tool(
        registry,
        ToolName::EnterIsolatedWorkspace,
        enter,
        text_spec(
            ToolName::EnterIsolatedWorkspace,
            &enter.description,
            schema_for!(EnterIsolatedWorkspaceInput),
        ),
        OutputShape::Text,
        Arc::new(EnterIsolatedWorkspace),
    );
}
