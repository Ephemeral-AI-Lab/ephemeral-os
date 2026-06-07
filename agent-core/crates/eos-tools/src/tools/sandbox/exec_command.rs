use async_trait::async_trait;
use eos_sandbox_port::ExecCommandRequest;
use eos_types::{InvocationId, JsonObject};
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
    command_tool_result, default_yield_ms, invalid_input, request_base, validate_command_timing,
};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct ExecCommandInput {
    cmd: String,
    #[serde(default = "default_yield_ms")]
    #[schemars(default = "default_yield_ms", range(max = 30000))]
    yield_time_ms: u32,
    #[serde(default)]
    #[schemars(range(min = 1))]
    timeout: Option<u32>,
}

pub(super) struct ExecCommand {
    service: CommandToolService,
}

impl ExecCommand {
    pub(super) fn new(service: CommandToolService) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for ExecCommand {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: ExecCommandInput = match parse_input(ToolName::ExecCommand, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if let Some(err) =
            validate_command_timing(ToolName::ExecCommand, parsed.yield_time_ms, parsed.timeout)
        {
            return Ok(err);
        }
        if parsed.cmd.is_empty() {
            return Ok(invalid_input(
                ToolName::ExecCommand,
                "cmd must be non-empty",
            ));
        }
        let sandbox_id = ctx.require_sandbox_id()?;
        let invocation_id = ctx
            .sandbox_invocation_id
            .clone()
            .unwrap_or_else(InvocationId::new_v4);
        let mut base = request_base(ctx, "exec_command")?;
        base.invocation_id = Some(invocation_id);
        let command = parsed.cmd.clone();
        let request = ExecCommandRequest {
            base,
            cmd: parsed.cmd,
            yield_time_ms: Some(parsed.yield_time_ms),
            timeout: parsed.timeout,
        };
        let result =
            match eos_sandbox_port::exec_command(&*self.service.transport, sandbox_id, &request)
                .await
            {
                Ok(result) => result,
                Err(err) => return Ok(ToolResult::error(err.to_string())),
            };
        // Register a backgrounded session on this run's command-session manager.
        // The manager is bound to the owning agent run, so no per-call agent-run
        // argument is threaded through the tool request.
        if let (Some(port), Some(session_id)) = (
            &self.service.command_session_port,
            &result.command_session_id,
        ) {
            if result.is_running() {
                port.register(session_id, sandbox_id, &command).await;
            }
        }
        Ok(command_tool_result(&result))
    }
}
