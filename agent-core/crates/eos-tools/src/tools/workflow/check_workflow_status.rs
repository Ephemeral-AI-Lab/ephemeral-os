//! The `check_workflow_status` control tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{JsonObject, WorkflowApi, WorkflowId};
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};

use crate::registry::config::ToolConfigSet;
use crate::registry::spec::text_spec;
use crate::runtime::execution::parse_input;
use eos_tool_ports::ExecutionMetadata;
use eos_tool_ports::ToolError;
use eos_tool_ports::ToolExecutor;
use eos_tool_ports::ToolName;
use eos_tool_ports::ToolRegistry;
use eos_tool_ports::{OutputShape, ToolResult};

use super::lib::empty_workflow_id_error;

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct CheckWorkflowStatusInput {
    workflow_id: WorkflowId,
}

pub(in crate::tools::workflow) struct CheckWorkflowStatus {
    workflow_service: Option<Arc<dyn WorkflowApi>>,
}

impl CheckWorkflowStatus {
    pub(in crate::tools::workflow) fn new(workflow_service: Option<Arc<dyn WorkflowApi>>) -> Self {
        Self { workflow_service }
    }
}

#[async_trait]
impl ToolExecutor for CheckWorkflowStatus {
    async fn execute(
        &self,
        input: &JsonObject,
        _ctx: &ExecutionMetadata,
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
        let output = self
            .workflow_service
            .as_deref()
            .ok_or(ToolError::MissingPort("workflow_service"))?
            .check_workflow_status(&parsed.workflow_id)
            .await?;
        Ok(ToolResult::ok(output))
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    workflow_service: Option<Arc<dyn WorkflowApi>>,
) {
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
        Arc::new(CheckWorkflowStatus::new(workflow_service)),
    );
}
