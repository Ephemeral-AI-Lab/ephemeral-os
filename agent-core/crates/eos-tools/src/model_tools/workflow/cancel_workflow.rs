//! The `cancel_workflow` control tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{JsonObject, WorkflowSessionId};
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

use super::lib::empty_workflow_id_error;

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct CancelWorkflowInput {
    workflow_task_id: WorkflowSessionId,
    #[serde(default)]
    reason: String,
}

pub(in crate::model_tools::workflow) struct CancelWorkflow;

#[async_trait]
impl ToolExecutor for CancelWorkflow {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: CancelWorkflowInput = match parse_input(ToolName::CancelWorkflow, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.workflow_task_id.as_str().is_empty() {
            return Ok(empty_workflow_id_error(
                ToolName::CancelWorkflow,
                "workflow_task_id",
            ));
        }
        let output = ctx
            .require_workflow_control()?
            .cancel(&parsed.workflow_task_id, &parsed.reason)
            .await?;
        if let Some(supervisor) = &ctx.background_supervisor {
            supervisor
                .cancel_workflow_record(&parsed.workflow_task_id, &parsed.reason)
                .await;
        }
        Ok(ToolResult::ok(output))
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let cancel = config.get(ToolName::CancelWorkflow);
    super::super::register_tool(
        registry,
        ToolName::CancelWorkflow,
        cancel,
        text_spec(
            ToolName::CancelWorkflow,
            &cancel.description,
            schema_for!(CancelWorkflowInput),
        ),
        OutputShape::Text,
        Arc::new(CancelWorkflow),
    );
}
