//! Sandbox command-session tools.

mod outputs {
    use std::collections::BTreeMap;

    use eos_types::{CommandSessionId, JsonObject};
    use schemars::JsonSchema;
    use serde::{Deserialize, Serialize};

    /// Shared output shape for command-session tools.
    #[derive(Debug, Serialize, Deserialize, JsonSchema)]
    pub(in crate::tools::command) struct CommandToolOutput {
        pub(in crate::tools::command) status: String,
        pub(in crate::tools::command) exit_code: Option<i32>,
        pub(in crate::tools::command) output: BTreeMap<String, String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        pub(in crate::tools::command) command_session_id: Option<CommandSessionId>,
        pub(in crate::tools::command) stdout: String,
        pub(in crate::tools::command) stderr: String,
        pub(in crate::tools::command) changed_paths: Vec<String>,
        pub(in crate::tools::command) changed_path_kinds: BTreeMap<String, String>,
        pub(in crate::tools::command) mutation_source: String,
        pub(in crate::tools::command) conflict_reason: Option<String>,
        pub(in crate::tools::command) error: Option<JsonObject>,
    }
}

use std::collections::BTreeMap;

use eos_sandbox_port::{ExecCommandResult, SandboxRequestBase};
use eos_types::JsonObject;
use serde::Serialize;
use serde_json::json;

use crate::ExecutionMetadata;
use crate::ToolError;
use crate::ToolName;
use crate::ToolResult;

use outputs::CommandToolOutput;

pub(super) const MAX_YIELD_TIME_MS: u32 = 30_000;

pub(super) fn request_base(
    ctx: &ExecutionMetadata,
    description: &str,
) -> Result<SandboxRequestBase, ToolError> {
    let agent_run_id = ctx.require_agent_run_id()?;
    Ok(SandboxRequestBase::new(
        agent_run_id.as_str(),
        description,
        ctx.sandbox_invocation_id.clone(),
    ))
}

pub(super) fn serialize_output<T: Serialize>(value: &T) -> Result<String, ToolResult> {
    serde_json::to_string(value)
        .map_err(|err| ToolResult::error(format!("failed to serialize tool output: {err}")))
}

pub(super) fn invalid_input(tool: ToolName, message: impl std::fmt::Display) -> ToolResult {
    ToolResult::error(format!(
        "Invalid input for {}: {message}. Please retry the tool call with valid arguments.",
        tool.as_str()
    ))
}

pub(super) fn default_yield_ms() -> u32 {
    1000
}

pub(super) fn validate_command_timing(
    tool: ToolName,
    yield_time_ms: u32,
    timeout: Option<u32>,
) -> Option<ToolResult> {
    if yield_time_ms > MAX_YIELD_TIME_MS {
        return Some(invalid_input(
            tool,
            format!("yield_time_ms must be <= {MAX_YIELD_TIME_MS}"),
        ));
    }
    if timeout == Some(0) {
        return Some(invalid_input(tool, "timeout must be >= 1"));
    }
    None
}

pub(super) fn command_tool_result(result: &ExecCommandResult) -> ToolResult {
    let is_error = result.is_error_status();
    let mut output_map = BTreeMap::new();
    output_map.insert("stdout".to_owned(), result.output.stdout.clone());
    output_map.insert("stderr".to_owned(), result.output.stderr.clone());
    let payload = CommandToolOutput {
        status: result.status.clone(),
        exit_code: result.exit_code,
        output: output_map,
        command_session_id: result.command_session_id.clone(),
        stdout: result.output.stdout.clone(),
        stderr: result.output.stderr.clone(),
        changed_paths: result.base.changed_paths.clone(),
        changed_path_kinds: result.changed_path_kinds.clone(),
        mutation_source: result.mutation_source.clone(),
        conflict_reason: result.base.conflict_reason.clone(),
        error: result.base.error.clone(),
    };
    let mut metadata = JsonObject::new();
    metadata.insert("status".to_owned(), json!(result.status));
    if let Some(id) = &result.command_session_id {
        metadata.insert("command_session_id".to_owned(), json!(id));
    }
    ToolResult {
        output: match serialize_output(&payload) {
            Ok(output) => output,
            Err(result) => return result,
        },
        is_error,
        metadata,
        is_terminal: false,
    }
}

mod exec_command {
    use async_trait::async_trait;
    use eos_sandbox_port::ExecCommandRequest;
    use eos_types::{InvocationId, JsonObject};
    use schemars::JsonSchema;
    use serde::{Deserialize, Serialize};

    use crate::tools::parse_input;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolResult;

    use super::{
        command_tool_result, default_yield_ms, invalid_input, request_base, validate_command_timing,
    };
    use crate::tools::CommandHandle;

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
        service: CommandHandle,
    }

    impl ExecCommand {
        pub(super) fn new(service: CommandHandle) -> Self {
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
            let request = ExecCommandRequest {
                base,
                cmd: parsed.cmd,
                yield_time_ms: Some(parsed.yield_time_ms),
                timeout: parsed.timeout,
            };
            let result = match self
                .service
                .command_service
                .exec_command(sandbox_id, &request)
                .await
            {
                Ok(result) => result,
                Err(err) => return Ok(ToolResult::error(err.to_string())),
            };
            // Register a backgrounded session on this run's command-session manager.
            // The manager is bound to the owning agent run, so no per-call agent-run
            // argument is threaded through the tool request.
            if let Some(session_id) = &result.command_session_id {
                if result.is_running() {
                    self.service
                        .register_command(session_id, sandbox_id)
                        .await?;
                }
            }
            Ok(command_tool_result(&result))
        }
    }
}
mod write_stdin {
    use async_trait::async_trait;
    use eos_sandbox_port::{CommandSessionCancelRequest, ExecStdinRequest};
    use eos_types::{CommandSessionId, JsonObject};
    use schemars::JsonSchema;
    use serde::{Deserialize, Serialize};

    use crate::tools::parse_input;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolResult;

    use super::{
        command_tool_result, default_yield_ms, invalid_input, request_base, validate_command_timing,
    };
    use crate::tools::CommandHandle;

    #[derive(Debug, Deserialize, Serialize, JsonSchema)]
    pub(super) struct WriteStdinInput {
        command_session_id: CommandSessionId,
        chars: String,
        #[serde(default = "default_yield_ms")]
        #[schemars(default = "default_yield_ms", range(max = 30000))]
        yield_time_ms: u32,
    }

    pub(super) struct WriteStdin {
        service: CommandHandle,
    }

    impl WriteStdin {
        pub(super) fn new(service: CommandHandle) -> Self {
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
            if let Some(err) =
                validate_command_timing(ToolName::WriteStdin, parsed.yield_time_ms, None)
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
}
mod read_command_progress {
    use async_trait::async_trait;
    use eos_sandbox_port::ReadCommandProgressRequest;
    use eos_types::{CommandSessionId, JsonObject};
    use schemars::JsonSchema;
    use serde::{Deserialize, Serialize};

    use crate::tools::parse_input;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolResult;

    use super::{command_tool_result, invalid_input, request_base};
    use crate::tools::CommandHandle;

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
        service: CommandHandle,
    }

    impl ReadCommandProgress {
        pub(super) fn new(service: CommandHandle) -> Self {
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
}

pub(crate) fn register(
    registry: &mut crate::ToolRegistry,
    config: &crate::registry::ToolConfigSet,
    command: crate::tools::CommandHandle,
) {
    use std::sync::Arc;

    use crate::registry::json_spec;
    use crate::{OutputShape, ToolName};
    use outputs::CommandToolOutput;
    use schemars::schema_for;

    let exec_command = config.get(ToolName::ExecCommand);
    crate::tools::register_tool(
        registry,
        ToolName::ExecCommand,
        exec_command,
        json_spec(
            ToolName::ExecCommand,
            &exec_command.description,
            schema_for!(exec_command::ExecCommandInput),
            schema_for!(CommandToolOutput),
        ),
        OutputShape::json::<CommandToolOutput>("CommandToolOutput"),
        Arc::new(exec_command::ExecCommand::new(command.clone())),
    );
    let write_stdin = config.get(ToolName::WriteStdin);
    crate::tools::register_tool(
        registry,
        ToolName::WriteStdin,
        write_stdin,
        json_spec(
            ToolName::WriteStdin,
            &write_stdin.description,
            schema_for!(write_stdin::WriteStdinInput),
            schema_for!(CommandToolOutput),
        ),
        OutputShape::json::<CommandToolOutput>("CommandToolOutput"),
        Arc::new(write_stdin::WriteStdin::new(command.clone())),
    );
    let read_command_progress = config.get(ToolName::ReadCommandProgress);
    crate::tools::register_tool(
        registry,
        ToolName::ReadCommandProgress,
        read_command_progress,
        json_spec(
            ToolName::ReadCommandProgress,
            &read_command_progress.description,
            schema_for!(read_command_progress::ReadCommandProgressInput),
            schema_for!(CommandToolOutput),
        ),
        OutputShape::json::<CommandToolOutput>("CommandToolOutput"),
        Arc::new(read_command_progress::ReadCommandProgress::new(command)),
    );
}

pub(crate) fn register_schema(
    registry: &mut crate::ToolRegistry,
    config: &crate::registry::ToolConfigSet,
) {
    use crate::registry::json_spec;
    use crate::{OutputShape, ToolName};
    use outputs::CommandToolOutput;
    use schemars::schema_for;

    let exec_command = config.get(ToolName::ExecCommand);
    crate::tools::register_schema_tool(
        registry,
        ToolName::ExecCommand,
        exec_command,
        json_spec(
            ToolName::ExecCommand,
            &exec_command.description,
            schema_for!(exec_command::ExecCommandInput),
            schema_for!(CommandToolOutput),
        ),
        OutputShape::json::<CommandToolOutput>("CommandToolOutput"),
    );
    let write_stdin = config.get(ToolName::WriteStdin);
    crate::tools::register_schema_tool(
        registry,
        ToolName::WriteStdin,
        write_stdin,
        json_spec(
            ToolName::WriteStdin,
            &write_stdin.description,
            schema_for!(write_stdin::WriteStdinInput),
            schema_for!(CommandToolOutput),
        ),
        OutputShape::json::<CommandToolOutput>("CommandToolOutput"),
    );
    let read_command_progress = config.get(ToolName::ReadCommandProgress);
    crate::tools::register_schema_tool(
        registry,
        ToolName::ReadCommandProgress,
        read_command_progress,
        json_spec(
            ToolName::ReadCommandProgress,
            &read_command_progress.description,
            schema_for!(read_command_progress::ReadCommandProgressInput),
            schema_for!(CommandToolOutput),
        ),
        OutputShape::json::<CommandToolOutput>("CommandToolOutput"),
    );
}
