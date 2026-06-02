//! Isolated-workspace lifecycle tools: `enter_isolated_workspace`,
//! `exit_isolated_workspace`. Both call the [`IsolatedWorkspacePort`] (the
//! `eos-runtime` adapter over the `eos-sandbox-host` lifecycle).

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

const ENTER_DESCRIPTION: &str = "Open a private isolated workspace for this agent.";
const EXIT_DESCRIPTION: &str = "Close and discard this agent's isolated workspace.";

fn default_grace_s() -> f64 {
    5.0
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct EnterIsolatedWorkspaceInput {
    #[serde(default)]
    layer_stack_root: String,
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct ExitIsolatedWorkspaceInput {
    #[serde(default = "default_grace_s")]
    #[schemars(default = "default_grace_s")]
    grace_s: f64,
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
        let agent_id = ctx.agent_id();
        let output = ctx
            .require_isolated_workspace()?
            .enter(&agent_id, sandbox_id, &parsed.layer_stack_root)
            .await?;
        Ok(ToolResult::ok(output))
    }
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
        let output = ctx
            .require_isolated_workspace()?
            .exit(&agent_id, sandbox_id, parsed.grace_s)
            .await?;
        Ok(ToolResult::ok(output))
    }
}

pub(crate) fn register(registry: &mut ToolRegistry) {
    super::register_tool(
        registry,
        ToolName::EnterIsolatedWorkspace,
        text_spec(
            ToolName::EnterIsolatedWorkspace,
            ENTER_DESCRIPTION,
            schema_for!(EnterIsolatedWorkspaceInput),
        ),
        OutputShape::Text,
        Arc::new(EnterIsolatedWorkspace),
    );
    super::register_tool(
        registry,
        ToolName::ExitIsolatedWorkspace,
        text_spec(
            ToolName::ExitIsolatedWorkspace,
            EXIT_DESCRIPTION,
            schema_for!(ExitIsolatedWorkspaceInput),
        ),
        OutputShape::Text,
        Arc::new(ExitIsolatedWorkspace),
    );
}
