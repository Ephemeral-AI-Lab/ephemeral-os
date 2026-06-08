//! The `delegate_workflow` launch tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use eos_types::{StartWorkflowRequest, WorkflowApi};

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
use crate::core::result::{OutputShape, ToolResult};
use crate::registry::config::ToolConfigSet;
use crate::registry::spec::text_spec;
use crate::registry::ToolRegistry;
use crate::runtime::execution::parse_input;
use crate::runtime::executor::ToolExecutor;
use crate::WorkflowToolService;

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct DelegateWorkflowInput {
    goal: String,
}

pub(in crate::tools::workflow) struct DelegateWorkflow {
    workflow_service: Option<Arc<dyn WorkflowApi>>,
    workflow_sessions: Option<WorkflowToolService>,
}

impl DelegateWorkflow {
    pub(in crate::tools::workflow) fn new(
        workflow_service: Option<Arc<dyn WorkflowApi>>,
        workflow_sessions: Option<WorkflowToolService>,
    ) -> Self {
        Self {
            workflow_service,
            workflow_sessions,
        }
    }
}

#[async_trait]
impl ToolExecutor for DelegateWorkflow {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: DelegateWorkflowInput = match parse_input(ToolName::DelegateWorkflow, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.goal.trim().is_empty() {
            return Ok(ToolResult::error("goal must be nonblank"));
        }
        let task_id = ctx.require_task_id()?;
        let agent_run_id = ctx.require_agent_run_id()?;
        let service = self
            .workflow_service
            .as_deref()
            .ok_or(ToolError::MissingPort("workflow_service"))?;
        let sessions = self
            .workflow_sessions
            .as_ref()
            .ok_or(ToolError::MissingPort("workflow_sessions"))?;

        let outstanding = service
            .find_outstanding_workflows(task_id, agent_run_id)
            .await?;
        if let Some(existing) = outstanding.first() {
            let payload = json!({
                "workflow_id": existing.workflow_id.as_str(),
                "status": "running",
                "message": "A delegated workflow is already outstanding for this task. \
                    Use check_workflow_status or cancel_workflow before starting another.",
            });
            return Ok(ToolResult::error(payload.to_string()));
        }

        let started = service
            .start_workflow(StartWorkflowRequest {
                parent_task_id: task_id.clone(),
                agent_run_id: agent_run_id.clone(),
                workflow_goal: parsed.goal.clone(),
            })
            .await?;
        sessions.register_background_session(&started).await?;
        let payload = json!({
            "workflow_id": started.workflow_id.as_str(),
            "status": "running",
            "message": format!(
                "Started delegated workflow {}. Use check_workflow_status to inspect progress \
                 or cancel_workflow to stop it.",
                started.workflow_id
            ),
        });
        let metadata: JsonObject = [
            ("submission_kind".to_owned(), json!("workflow_delegated")),
            (
                "workflow_id".to_owned(),
                json!(started.workflow_id.as_str()),
            ),
            ("task_id".to_owned(), json!(task_id.as_str())),
        ]
        .into_iter()
        .collect();
        Ok(ToolResult::ok(payload.to_string()).with_metadata(metadata))
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    workflow_service: Option<Arc<dyn WorkflowApi>>,
    workflow_sessions: Option<WorkflowToolService>,
) {
    let delegate = config.get(ToolName::DelegateWorkflow);
    super::super::register_tool(
        registry,
        ToolName::DelegateWorkflow,
        delegate,
        text_spec(
            ToolName::DelegateWorkflow,
            &delegate.description,
            schema_for!(DelegateWorkflowInput),
        ),
        OutputShape::Text,
        Arc::new(DelegateWorkflow::new(workflow_service, workflow_sessions)),
    );
}
