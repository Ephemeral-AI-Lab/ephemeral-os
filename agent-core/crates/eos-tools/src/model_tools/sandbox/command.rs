use std::collections::BTreeMap;

use async_trait::async_trait;
use eos_sandbox_api::{ExecCommandRequest, ExecCommandResult, ExecStdinRequest};
use eos_types::{CommandSessionId, InvocationId, JsonObject};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::error::ToolError;
use crate::execution::parse_input;
use crate::executor::ToolExecutor;
use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::result::ToolResult;

use super::common::{invalid_input, request_base, serialize_output, MAX_YIELD_TIME_MS};
use super::outputs::CommandToolOutput;

fn default_yield_ms() -> u32 {
    1000
}

fn validate_command_timing(
    tool: ToolName,
    yield_time_ms: u32,
    timeout: Option<u32>,
    max_output_tokens: Option<u32>,
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
    if max_output_tokens == Some(0) {
        return Some(invalid_input(tool, "max_output_tokens must be >= 1"));
    }
    None
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct ExecCommandInput {
    cmd: String,
    #[serde(default = "default_yield_ms")]
    #[schemars(default = "default_yield_ms", range(max = 30000))]
    yield_time_ms: u32,
    #[serde(default)]
    #[schemars(range(min = 1))]
    timeout: Option<u32>,
    #[serde(default)]
    #[schemars(range(min = 1))]
    max_output_tokens: Option<u32>,
}

pub(super) struct ExecCommand;

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
        if let Some(err) = validate_command_timing(
            ToolName::ExecCommand,
            parsed.yield_time_ms,
            parsed.timeout,
            parsed.max_output_tokens,
        ) {
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
        let mut base = request_base(ctx, "exec_command");
        base.invocation_id = Some(invocation_id);
        let command = parsed.cmd.clone();
        let request = ExecCommandRequest {
            base,
            cmd: parsed.cmd,
            yield_time_ms: Some(parsed.yield_time_ms),
            timeout: parsed.timeout,
            max_output_tokens: parsed.max_output_tokens,
        };
        let result =
            match eos_sandbox_api::exec_command(&*ctx.transport, sandbox_id, &request).await {
                Ok(result) => result,
                Err(err) => return Ok(ToolResult::error(err.to_string())),
            };
        // Register a backgrounded session with the supervisor so the heartbeat
        // pulls its completion. The daemon scopes the session under the RPC's
        // top-level `agent_id` (== `caller.agent_id`), so register under the same
        // id or the heartbeat's `collect_completed` filter would never match.
        if let (Some(port), Some(session_id)) =
            (&ctx.command_session_supervisor, &result.command_session_id)
        {
            if result.status == "running" {
                port.register(
                    session_id,
                    sandbox_id.as_str(),
                    &ctx.caller.agent_id,
                    &command,
                )
                .await;
            }
        }
        Ok(command_tool_result(&result))
    }
}

fn default_false() -> bool {
    false
}

fn default_chars() -> String {
    String::new()
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct WriteStdinInput {
    command_session_id: CommandSessionId,
    #[serde(default = "default_chars")]
    chars: String,
    #[serde(default = "default_yield_ms")]
    #[schemars(default = "default_yield_ms", range(max = 30000))]
    yield_time_ms: u32,
    #[serde(default)]
    #[schemars(range(min = 1))]
    max_output_tokens: Option<u32>,
    /// Tear the session down after writing. A `\x03` char only interrupts
    /// (SIGINT); set this to end the session (SIGTERM→SIGKILL).
    #[serde(default = "default_false")]
    terminate: bool,
}

pub(super) struct WriteStdin;

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
        if let Some(err) = validate_command_timing(
            ToolName::WriteStdin,
            parsed.yield_time_ms,
            None,
            parsed.max_output_tokens,
        ) {
            return Ok(err);
        }
        if parsed.command_session_id.as_str().is_empty() {
            return Ok(invalid_input(
                ToolName::WriteStdin,
                "command_session_id must be non-empty",
            ));
        }
        let command_session_id = parsed.command_session_id.into_inner();
        let sandbox_id = ctx.require_sandbox_id()?;
        // Ctrl-C decoupling (sense-2 D7): `\x03` rides through as ordinary stdin
        // and the daemon raises SIGINT; teardown is the explicit `terminate`
        // flag (SIGTERM→SIGKILL), so the tool no longer escalates to a cancel RPC.
        let write_request = ExecStdinRequest {
            base: request_base(ctx, "write_stdin"),
            command_session_id: command_session_id.clone(),
            chars: parsed.chars.clone(),
            yield_time_ms: Some(parsed.yield_time_ms),
            max_output_tokens: parsed.max_output_tokens,
            terminate: parsed.terminate,
        };
        let result =
            match eos_sandbox_api::exec_stdin(&*ctx.transport, sandbox_id, &write_request).await {
                Ok(result) => result,
                Err(err) => return Ok(ToolResult::error(err.to_string())),
            };
        // Recover race + exactly-once latch (anchor §8). If the daemon already
        // lost the live session, surface the supervisor's stored terminal;
        // otherwise, once a terminal status is observed inline, latch it
        // `Delivered` so the heartbeat never re-notifies the same completion.
        if let Some(port) = &ctx.command_session_supervisor {
            if is_command_session_not_found(&result) {
                // Already surfaced via the heartbeat `[BACKGROUND COMPLETED]` —
                // a terse note, not the full payload again (anchor §8/D8).
                if port
                    .command_session_already_reported(&command_session_id)
                    .await
                {
                    return Ok(ToolResult::ok(format!(
                        "Command session {command_session_id} already completed; \
                         its result was already reported."
                    )));
                }
                if let Some(stored) = port.command_session_result(&command_session_id).await {
                    port.mark_command_session_reported(&command_session_id, stored.clone())
                        .await;
                    return Ok(command_tool_result_from_value(&stored));
                }
            } else if result.status != "running" {
                port.mark_command_session_reported(
                    &command_session_id,
                    command_result_value(&result),
                )
                .await;
            }
        }
        Ok(command_tool_result(&result))
    }
}

/// Whether a `write_stdin` result is the daemon's "live session is gone" signal
/// (`command_session_not_found`), so the supervisor's stored terminal can be
/// recovered.
fn is_command_session_not_found(result: &ExecCommandResult) -> bool {
    result.status == "error" && result.output.stderr.contains("command_session_not_found")
}

/// Project an [`ExecCommandResult`] into the daemon completion `result` shape the
/// supervisor stores (status / `exit_code` / `output`).
fn command_result_value(result: &ExecCommandResult) -> Value {
    json!({
        "status": result.status,
        "exit_code": result.exit_code,
        "output": {
            "stdout": result.output.stdout,
            "stderr": result.output.stderr,
        },
    })
}

/// Render a supervisor-stored terminal `result` value into the tool output DTO
/// (the recover-race return path).
fn command_tool_result_from_value(result: &Value) -> ToolResult {
    let status = result
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("ok")
        .to_owned();
    let exit_code = result
        .get("exit_code")
        .and_then(Value::as_i64)
        .map(|code| code as i32);
    let stdout = result
        .get("output")
        .and_then(|output| output.get("stdout"))
        .and_then(Value::as_str)
        .or_else(|| result.get("stdout").and_then(Value::as_str))
        .unwrap_or("")
        .to_owned();
    let stderr = result
        .get("output")
        .and_then(|output| output.get("stderr"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();
    let is_error = matches!(status.as_str(), "error" | "timed_out");
    let mut output_map = BTreeMap::new();
    output_map.insert("stdout".to_owned(), stdout.clone());
    output_map.insert("stderr".to_owned(), stderr.clone());
    let payload = CommandToolOutput {
        status: status.clone(),
        exit_code,
        output: output_map,
        command_session_id: None,
        stdout,
        stderr,
        changed_paths: Vec::new(),
        changed_path_kinds: BTreeMap::new(),
        mutation_source: String::new(),
        conflict_reason: None,
        error: None,
    };
    let mut metadata = JsonObject::new();
    metadata.insert("status".to_owned(), json!(status));
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

/// `command_tool_result`.
fn command_tool_result(result: &ExecCommandResult) -> ToolResult {
    let is_error = matches!(result.status.as_str(), "error" | "timed_out");
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
