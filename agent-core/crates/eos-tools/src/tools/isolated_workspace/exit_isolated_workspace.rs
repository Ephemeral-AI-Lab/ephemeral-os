//! The `exit_isolated_workspace` lifecycle tool.

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

fn default_grace_s() -> f64 {
    5.0
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct ExitIsolatedWorkspaceInput {
    #[serde(default = "default_grace_s")]
    #[schemars(default = "default_grace_s")]
    grace_s: f64,
}

struct ExitIsolatedWorkspace;

#[async_trait]
impl ToolExecutor for ExitIsolatedWorkspace {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: ExitIsolatedWorkspaceInput =
            match parse_input(ToolName::ExitIsolatedWorkspace, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        let sandbox_id = ctx.require_sandbox_id()?;
        let agent_id = ctx.agent_id();
        ctx.require_isolated_workspace()?
            .exit(&agent_id, sandbox_id, parsed.grace_s)
            .await
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let exit = config.get(ToolName::ExitIsolatedWorkspace);
    super::super::register_tool(
        registry,
        ToolName::ExitIsolatedWorkspace,
        exit,
        text_spec(
            ToolName::ExitIsolatedWorkspace,
            &exit.description,
            schema_for!(ExitIsolatedWorkspaceInput),
        ),
        OutputShape::Text,
        Arc::new(ExitIsolatedWorkspace),
    );
}
