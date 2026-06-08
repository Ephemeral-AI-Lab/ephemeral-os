use async_trait::async_trait;
use eos_sandbox_port::{CommandSessionCancelRequest, ExecStdinRequest};
use eos_types::{CommandSessionId, JsonObject};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::runtime::execution::parse_input;
use eos_tool_ports::ExecutionMetadata;
use eos_tool_ports::ToolError;
use eos_tool_ports::ToolExecutor;
use eos_tool_ports::ToolName;
use eos_tool_ports::ToolResult;

use super::super::CommandToolService;
use super::lib::{
    command_tool_result, default_yield_ms, invalid_input, request_base, validate_command_timing,
};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct WriteStdinInput {
    command_session_id: CommandSessionId,
    chars: String,
    #[serde(default = "default_yield_ms")]
    #[schemars(default = "default_yield_ms", range(max = 30000))]
    yield_time_ms: u32,
}

pub(super) struct WriteStdin {
    service: CommandToolService,
}

impl WriteStdin {
    pub(super) fn new(service: CommandToolService) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for WriteStdin {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: WriteStdinInput = match parse_input(ToolName::WriteStdin, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.command_session_id.as_str().is_empty() {
            return Ok(invalid_input(
                ToolName::WriteStdin,
                "command_session_id must be non-empty",
            ));
        }
        if let Some(err) = validate_command_timing(ToolName::WriteStdin, parsed.yield_time_ms, None)
        {
            return Ok(err);
        }
        if parsed.chars.is_empty() {
            return Ok(invalid_input(
                ToolName::WriteStdin,
                "chars must be non-empty",
            ));
        }
        let command_session_id = &parsed.command_session_id;
        let sandbox_id = ctx.require_sandbox_id()?;

        let result = if is_teardown_control(&parsed.chars) {
            let request = CommandSessionCancelRequest {
                base: request_base(ctx, "write_stdin")?,
                command_session_id: command_session_id.clone(),
            };
            self.service
                .command_service
                .cancel_command_session(sandbox_id, &request)
                .await
        } else if contains_teardown_control(&parsed.chars) {
            return Ok(invalid_input(
                ToolName::WriteStdin,
                "Ctrl-C/Ctrl-D must be sent alone to cancel command session",
            ));
        } else {
            let request = ExecStdinRequest {
                base: request_base(ctx, "write_stdin")?,
                command_session_id: command_session_id.clone(),
                chars: parsed.chars.clone(),
                yield_time_ms: Some(parsed.yield_time_ms),
            };
            self.service
                .command_service
                .write_stdin(sandbox_id, &request)
                .await
        };
        let result = match result {
            Ok(result) => result,
            Err(err) => return Ok(ToolResult::error(err.to_string())),
        };
        Ok(command_tool_result(&result))
    }
}

fn is_teardown_control(chars: &str) -> bool {
    matches!(chars, "\u{3}" | "\u{4}")
}

fn contains_teardown_control(chars: &str) -> bool {
    chars.contains('\u{3}') || chars.contains('\u{4}')
}
