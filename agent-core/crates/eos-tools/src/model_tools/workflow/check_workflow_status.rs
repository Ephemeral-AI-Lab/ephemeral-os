//! The `check_workflow_status` control tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{JsonObject, WorkflowId, WorkflowSessionId};
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
struct CheckWorkflowStatusInput {
    workflow_id: WorkflowId,
    #[serde(default)]
    workflow_task_id: Option<WorkflowSessionId>,
}

pub(in crate::model_tools::workflow) struct CheckWorkflowStatus;

#[async_trait]
impl ToolExecutor for CheckWorkflowStatus {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: CheckWorkflowStatusInput =
            match parse_input(ToolName::CheckWorkflowStatus, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        if parsed.workflow_id.as_str().is_empty() {
            return Ok(empty_workflow_id_error(
                ToolName::CheckWorkflowStatus,
                "workflow_id",
            ));
        }
        if parsed
            .workflow_task_id
            .as_ref()
            .is_some_and(|id| id.as_str().is_empty())
        {
            return Ok(empty_workflow_id_error(
                ToolName::CheckWorkflowStatus,
                "workflow_task_id",
            ));
        }
        let output = ctx
            .require_workflow_control()?
            .status(&parsed.workflow_id, parsed.workflow_task_id.as_ref())
            .await?;
        Ok(ToolResult::ok(output))
    }
}

pub(super) fn register(registry: &mut ToolRegistry, config: &ToolConfigSet) {
    let check = config.get(ToolName::CheckWorkflowStatus);
    super::super::register_tool(
        registry,
        ToolName::CheckWorkflowStatus,
        check,
        text_spec(
            ToolName::CheckWorkflowStatus,
            &check.description,
            schema_for!(CheckWorkflowStatusInput),
        ),
        OutputShape::Text,
        Arc::new(CheckWorkflowStatus),
    );
}
