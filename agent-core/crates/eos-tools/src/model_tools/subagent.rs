//! Subagent tools: `run_subagent` (the restricted, caller-scoped dispatch),
//! `check_subagent_progress`, `cancel_subagent`. All call the
//! [`SubagentSupervisorPort`].
//!
//! `run_subagent` is the restricted variant: its `agent_name` input schema is
//! patched per caller with the `enum` of dispatchable subagents (§6.6). The
//! downstream validation (caller is not a subagent, the agent exists and is a
//! subagent) lives in the port implementor (`eos-engine`, which has the agent
//! registry that `eos-tools` deliberately does not depend on).

use std::sync::Arc;

use async_trait::async_trait;
use eos_types::{JsonObject, SubagentSessionId};
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use super::CallerScope;
use crate::error::ToolError;
use crate::execution::parse_input;
use crate::executor::ToolExecutor;
use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::registry::ToolRegistry;
use crate::result::{OutputShape, ToolResult};
use crate::spec::{text_spec, text_spec_with_agent_enum};

const RUN_SUBAGENT_DESCRIPTION: &str = include_str!("descriptions/run_subagent.md");
const CHECK_DESCRIPTION: &str = "Check a running or finished subagent by subagent_session_id. Returns the latest child-agent message snapshot while running and the terminal result after successful completion.";
const CANCEL_DESCRIPTION: &str = "Cancel a running subagent by subagent_session_id.";

fn default_five() -> u8 {
    5
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct RunSubagentInput {
    /// Name of a registered dispatchable subagent (caller-scoped enum, §6.6).
    agent_name: String,
    prompt: String,
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct CheckSubagentProgressInput {
    subagent_session_id: SubagentSessionId,
    #[serde(default = "default_five")]
    #[schemars(default = "default_five")]
    last_n_messages: u8,
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct CancelSubagentInput {
    subagent_session_id: SubagentSessionId,
    #[serde(default)]
    reason: String,
}

struct RunSubagent;

#[async_trait]
impl ToolExecutor for RunSubagent {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: RunSubagentInput = match parse_input(ToolName::RunSubagent, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.prompt.trim().is_empty() {
            return Ok(ToolResult::error(
                "run_subagent: `prompt` must be a non-empty string.",
            ));
        }
        let outcome = ctx
            .require_subagent_supervisor()?
            .run(&parsed.agent_name, &parsed.prompt)
            .await?;
        let mut metadata = JsonObject::new();
        metadata.insert(
            "subagent_terminal_called".to_owned(),
            json!(outcome.terminal_called),
        );
        Ok(ToolResult {
            output: outcome.output,
            is_error: outcome.is_error,
            metadata,
            is_terminal: false,
        })
    }
}

struct CheckSubagentProgress;

#[async_trait]
impl ToolExecutor for CheckSubagentProgress {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: CheckSubagentProgressInput =
            match parse_input(ToolName::CheckSubagentProgress, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        let output = ctx
            .require_subagent_supervisor()?
            .progress(&parsed.subagent_session_id, parsed.last_n_messages)
            .await?;
        Ok(ToolResult::ok(output))
    }
}

struct CancelSubagent;

#[async_trait]
impl ToolExecutor for CancelSubagent {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: CancelSubagentInput = match parse_input(ToolName::CancelSubagent, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        let output = ctx
            .require_subagent_supervisor()?
            .cancel(&parsed.subagent_session_id, &parsed.reason)
            .await?;
        Ok(ToolResult::ok(output))
    }
}

pub(crate) fn register(registry: &mut ToolRegistry, caller: &CallerScope) {
    super::register_tool(
        registry,
        ToolName::RunSubagent,
        text_spec_with_agent_enum(
            ToolName::RunSubagent,
            RUN_SUBAGENT_DESCRIPTION,
            schema_for!(RunSubagentInput),
            &caller.dispatchable_subagents,
        ),
        OutputShape::Text,
        Arc::new(RunSubagent),
    );
    super::register_tool(
        registry,
        ToolName::CheckSubagentProgress,
        text_spec(
            ToolName::CheckSubagentProgress,
            CHECK_DESCRIPTION,
            schema_for!(CheckSubagentProgressInput),
        ),
        OutputShape::Text,
        Arc::new(CheckSubagentProgress),
    );
    super::register_tool(
        registry,
        ToolName::CancelSubagent,
        text_spec(
            ToolName::CancelSubagent,
            CANCEL_DESCRIPTION,
            schema_for!(CancelSubagentInput),
        ),
        OutputShape::Text,
        Arc::new(CancelSubagent),
    );
}
