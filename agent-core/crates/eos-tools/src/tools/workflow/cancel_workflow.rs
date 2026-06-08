//! The `cancel_workflow` control tool.

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
struct CancelWorkflowInput {
    workflow_id: WorkflowId,
    #[serde(default)]
    reason: String,
}

pub(in crate::tools::workflow) struct CancelWorkflow {
    workflow_service: Option<Arc<dyn WorkflowApi>>,
}

impl CancelWorkflow {
    pub(in crate::tools::workflow) fn new(workflow_service: Option<Arc<dyn WorkflowApi>>) -> Self {
        Self { workflow_service }
    }
}

#[async_trait]
impl ToolExecutor for CancelWorkflow {
    async fn execute(
        &self,
        input: &JsonObject,
        _ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: CancelWorkflowInput = match parse_input(ToolName::CancelWorkflow, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.workflow_id.as_str().is_empty() {
            return Ok(empty_workflow_id_error(
                ToolName::CancelWorkflow,
                "workflow_id",
            ));
        }
        let output = self
            .workflow_service
            .as_deref()
            .ok_or(ToolError::MissingPort("workflow_service"))?
            .cancel_workflow(&parsed.workflow_id, &parsed.reason)
            .await?;
        Ok(ToolResult::ok(output))
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    workflow_service: Option<Arc<dyn WorkflowApi>>,
) {
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
        Arc::new(CancelWorkflow::new(workflow_service)),
    );
}
