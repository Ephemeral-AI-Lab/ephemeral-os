use async_trait::async_trait;
use eos_sandbox_port::{CommandSessionCancelRequest, ExecStdinRequest};
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
use super::lib::{
    command_result_value, command_tool_result, command_tool_result_from_value, default_yield_ms,
    invalid_input, is_command_session_not_found, request_base, validate_command_timing,
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
            eos_sandbox_port::cancel_command_session(&*self.service.transport, sandbox_id, &request)
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
            eos_sandbox_port::exec_stdin(&*self.service.transport, sandbox_id, &request).await
        };
        let result = match result {
            Ok(result) => result,
            Err(err) => return Ok(ToolResult::error(err.to_string())),
        };
        // If the daemon already lost the live session, surface the supervisor's
        // stored terminal; otherwise, once a terminal status is observed inline,
        // latch it as delivered so the heartbeat never re-notifies the same result.
        if let Some(port) = &self.service.command_session_supervisor {
            if is_command_session_not_found(&result) {
                if port
                    .command_session_already_reported(command_session_id)
                    .await
                {
                    return Ok(ToolResult::ok(format!(
                        "Command session {command_session_id} already completed; \
                         its result was already reported."
                    )));
                }
                if let Some(stored) = port.command_session_result(command_session_id).await {
                    port.mark_command_session_reported(command_session_id, stored.clone())
                        .await;
                    return Ok(command_tool_result_from_value(&stored));
                }
            } else if !result.is_running() {
                port.mark_command_session_reported(
                    command_session_id,
                    command_result_value(&result),
                )
                .await;
            }
        }
        Ok(command_tool_result(&result))
    }
}

fn is_teardown_control(chars: &str) -> bool {
    matches!(chars, "\u{3}" | "\u{4}")
}

fn contains_teardown_control(chars: &str) -> bool {
    chars.contains('\u{3}') || chars.contains('\u{4}')
}
