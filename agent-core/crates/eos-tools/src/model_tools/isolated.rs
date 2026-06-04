//! Isolated-workspace lifecycle tools: `enter_isolated_workspace`,
//! `exit_isolated_workspace`. Both call the [`IsolatedWorkspacePort`] (the
//! `eos-runtime` adapter over the `eos-sandbox-host` lifecycle).

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::JsonObject;
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
        ctx.require_isolated_workspace()?
            .enter(&agent_id, sandbox_id, &parsed.layer_stack_root)
            .await
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
        ctx.require_isolated_workspace()?
            .exit(&agent_id, sandbox_id, parsed.grace_s)
            .await
    }
}

pub(crate) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let enter = config.get(ToolName::EnterIsolatedWorkspace);
    super::register_tool(
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
    let exit = config.get(ToolName::ExitIsolatedWorkspace);
    super::register_tool(
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
