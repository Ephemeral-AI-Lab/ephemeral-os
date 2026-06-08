//! The `ask_advisor` helper tool — a blocking read-only advisor audit of a
//! pending terminal submission.
//!
//! Execution spawns the advisor agent through the agent-run API and
//! waits for its terminal outcome before returning a non-terminal parent result.

use std::sync::Arc;

use async_trait::async_trait;
use eos_agent_ports::{
    AgentName, AgentRunApi, AgentRunError, AgentRunOutcome, AgentRunRecordKind, SpawnAgentRequest,
};
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
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
    agent_run_service: Option<Arc<dyn AgentRunApi>>,
}

impl AskAdvisor {
    fn new(agent_run_service: Option<Arc<dyn AgentRunApi>>) -> Self {
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
        let parent_agent_run_id = ctx.agent_run_id.clone();
        let advisor_run_id = match agent_run_service
            .spawn_agent(SpawnAgentRequest {
                agent_name: AgentName::new("advisor").expect("advisor agent name is valid"),
                agent_run_id: None,
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
            Err(err) => return Ok(advisor_spawn_error(&err)),
        };

        let advisor_result = match agent_run_service
            .wait_for_agent_outcome(&advisor_run_id)
            .await
        {
            Ok(outcome) => advisor_outcome_to_tool_result(outcome),
            Err(err) => {
                return Ok(ToolResult::error(format!(
                    "ask_advisor: advisor crashed: {err}"
                )))
            }
        };

        Ok(advisor_result)
    }
}

fn advisor_spawn_error(err: &AgentRunError) -> ToolResult {
    match err {
        AgentRunError::AgentNotRegistered(_) => {
            ToolResult::error("ask_advisor: agent definition 'advisor' not registered.")
        }
        _ => ToolResult::error(format!("ask_advisor: {err}")),
    }
}

fn advisor_outcome_to_tool_result(outcome: AgentRunOutcome) -> ToolResult {
    let Some(payload) = outcome.submission_payload.as_ref() else {
        return ToolResult::error(
            outcome.error.unwrap_or_else(|| {
                "ask_advisor: advisor exited without terminal output".to_owned()
            }),
        );
    };

    let mut result = tool_result_from_payload(payload);
    result.is_terminal = false;
    result
}

fn tool_result_from_payload(payload: &JsonObject) -> ToolResult {
    let output = payload
        .get("output")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let is_error = payload
        .get("is_error")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let metadata = payload
        .get("metadata")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    ToolResult {
        output,
        is_error,
        metadata,
        is_terminal: false,
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    agent_run_service: Option<Arc<dyn AgentRunApi>>,
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
