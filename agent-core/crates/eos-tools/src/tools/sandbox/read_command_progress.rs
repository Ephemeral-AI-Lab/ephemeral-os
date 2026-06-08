use async_trait::async_trait;
use eos_sandbox_port::ReadCommandProgressRequest;
use eos_types::{CommandSessionId, JsonObject};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
use crate::core::result::ToolResult;
use crate::runtime::execution::parse_input;
use crate::runtime::executor::ToolExecutor;

use super::super::CommandToolService;
use super::lib::{command_tool_result, invalid_input, request_base};

const MAX_LAST_N_LINES: u32 = 200;

fn default_last_n_lines() -> u32 {
    50
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct ReadCommandProgressInput {
    command_session_id: CommandSessionId,
    #[serde(default = "default_last_n_lines")]
    #[schemars(default = "default_last_n_lines", range(min = 1, max = 200))]
    last_n_lines: u32,
}

pub(super) struct ReadCommandProgress {
    service: CommandToolService,
}

impl ReadCommandProgress {
    pub(super) fn new(service: CommandToolService) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for ReadCommandProgress {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: ReadCommandProgressInput =
            match parse_input(ToolName::ReadCommandProgress, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        if parsed.command_session_id.as_str().is_empty() {
            return Ok(invalid_input(
                ToolName::ReadCommandProgress,
                "command_session_id must be non-empty",
            ));
        }
        if parsed.last_n_lines == 0 || parsed.last_n_lines > MAX_LAST_N_LINES {
            return Ok(invalid_input(
                ToolName::ReadCommandProgress,
                format!("last_n_lines must be between 1 and {MAX_LAST_N_LINES}"),
            ));
        }
        let command_session_id = &parsed.command_session_id;
        let sandbox_id = ctx.require_sandbox_id()?;
        let request = ReadCommandProgressRequest {
            base: request_base(ctx, "read_command_progress")?,
            command_session_id: command_session_id.clone(),
            last_n_lines: parsed.last_n_lines,
        };
        let result = match self
            .service
            .command_service
            .read_command_progress(sandbox_id, &request)
            .await
        {
            Ok(result) => result,
            Err(err) => return Ok(ToolResult::error(err.to_string())),
        };
        Ok(command_tool_result(&result))
    }
}
