//! The `ask_advisor` helper tool — a blocking read-only advisor audit of a
//! pending terminal submission.
//!
//! Execution spawns the advisor agent through the agent-run service bridge and
//! waits for its terminal outcome before returning a non-terminal parent result.

use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_def::AgentName;
use eos_agent_message_records::AgentRunRecordKind;
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
use crate::core::ports::{AgentRunServicePort, AgentSpawnError, SpawnAgentRequest};
use crate::core::result::{OutputShape, ToolResult};
use crate::registry::config::ToolConfigSet;
use crate::registry::spec::text_spec;
use crate::registry::ToolRegistry;
use crate::runtime::execution::parse_input;
use crate::runtime::executor::ToolExecutor;

use super::advisor_prompt::build_advisor_messages;

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct AskAdvisorInput {
    /// The terminal tool the caller intends to call.
    tool_name: String,
    /// The arguments the caller intends to pass.
    #[serde(default)]
    tool_payload: JsonObject,
}

struct AskAdvisor {
    agent_run_service: Option<Arc<dyn AgentRunServicePort>>,
}

impl AskAdvisor {
    fn new(agent_run_service: Option<Arc<dyn AgentRunServicePort>>) -> Self {
        Self { agent_run_service }
    }
}

#[async_trait]
impl ToolExecutor for AskAdvisor {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: AskAdvisorInput = match parse_input(ToolName::AskAdvisor, input) {
            Ok(parsed) => parsed,
            Err(err) => return Ok(err),
        };
        if parsed.tool_name.trim().is_empty() {
            return Ok(ToolResult::error("tool_name must be nonblank"));
        }

        let Some(agent_run_service) = self.agent_run_service.as_deref() else {
            return Ok(ToolResult::error(
                "ask_advisor is unavailable: the agent-run service is not wired for this run",
            ));
        };
        let Ok(advisor_name) = AgentName::new("advisor") else {
            return Ok(ToolResult::error(
                "ask_advisor: agent definition 'advisor' not registered.",
            ));
        };

        let parent_agent_run_id = ctx.agent_run_id.clone();
        let advisor_run_id = match agent_run_service
            .spawn_agent(SpawnAgentRequest {
                agent_name: advisor_name,
                initial_messages: build_advisor_messages(
                    ctx,
                    &parsed.tool_name,
                    &parsed.tool_payload,
                ),
                parent_agent_run_id: parent_agent_run_id.clone(),
                request_id: ctx.request_id.clone(),
                task_id: None,
                attempt_id: None,
                workflow_id: None,
                sandbox_id: ctx.sandbox_id.clone(),
                workspace_root: ctx.workspace_root.clone(),
                is_isolated_workspace_mode: false,
                persist: true,
                record_kind: parent_agent_run_id
                    .map(|parent_agent_run_id| AgentRunRecordKind::Advisor {
                        parent_agent_run_id,
                    })
                    .unwrap_or(AgentRunRecordKind::Agent),
            })
            .await
        {
            Ok(agent_run_id) => agent_run_id,
            Err(err) => return Ok(advisor_spawn_error(err)),
        };

        let advisor_result = match agent_run_service
            .wait_for_agent_result(&advisor_run_id)
            .await
        {
            Ok(result) => result,
            Err(err) => {
                return Ok(ToolResult::error(format!(
                    "ask_advisor: advisor crashed: {err}"
                )))
            }
        };

        Ok(ToolResult {
            output: advisor_result.output,
            is_error: advisor_result.is_error,
            metadata: advisor_result.metadata,
            is_terminal: false,
        })
    }
}

fn advisor_spawn_error(err: AgentSpawnError) -> ToolResult {
    match err {
        AgentSpawnError::Rejected(_) => {
            ToolResult::error("ask_advisor: agent definition 'advisor' not registered.")
        }
        AgentSpawnError::Tool(err) => ToolResult::error(format!("ask_advisor: {err}")),
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    agent_run_service: Option<Arc<dyn AgentRunServicePort>>,
) {
    let ask_advisor = config.get(ToolName::AskAdvisor);
    super::super::register_tool(
        registry,
        ToolName::AskAdvisor,
        ask_advisor,
        text_spec(
            ToolName::AskAdvisor,
            &ask_advisor.description,
            schema_for!(AskAdvisorInput),
        ),
        OutputShape::Text,
        Arc::new(AskAdvisor::new(agent_run_service)),
    );
}
