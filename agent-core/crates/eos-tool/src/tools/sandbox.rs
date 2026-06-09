//! Sandbox file/edit and isolated-workspace tools.

mod outputs {
use std::collections::BTreeMap;

use eos_types::{CommandSessionId, JsonObject};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(in crate::tools::sandbox) struct ReadFileOutput {
    pub(in crate::tools::sandbox) cwd: String,
    pub(in crate::tools::sandbox) file_path: String,
    pub(in crate::tools::sandbox) total_lines: u32,
    pub(in crate::tools::sandbox) start_line: u32,
    pub(in crate::tools::sandbox) end_line: u32,
    pub(in crate::tools::sandbox) content: String,
}

#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(in crate::tools::sandbox) struct MutationOutput {
    pub(in crate::tools::sandbox) cwd: String,
    pub(in crate::tools::sandbox) file_path: String,
    pub(in crate::tools::sandbox) status: String,
    pub(in crate::tools::sandbox) changed_paths: Vec<String>,
    pub(in crate::tools::sandbox) changed_path_kinds: BTreeMap<String, String>,
    pub(in crate::tools::sandbox) mutation_source: String,
    pub(in crate::tools::sandbox) conflict_reason: Option<String>,
    pub(in crate::tools::sandbox) error: JsonObject,
    /// `bytes_written` for `write_file`, `applied_edits` for the edit tools.
    #[serde(flatten)]
    pub(in crate::tools::sandbox) extra: BTreeMap<String, Value>,
}

/// Shared output shape for command-session tools.
#[derive(Debug, Serialize, Deserialize, JsonSchema)]
pub(in crate::tools::sandbox) struct CommandToolOutput {
    pub(in crate::tools::sandbox) status: String,
    pub(in crate::tools::sandbox) exit_code: Option<i32>,
    pub(in crate::tools::sandbox) output: BTreeMap<String, String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(in crate::tools::sandbox) command_session_id: Option<CommandSessionId>,
    pub(in crate::tools::sandbox) stdout: String,
    pub(in crate::tools::sandbox) stderr: String,
    pub(in crate::tools::sandbox) changed_paths: Vec<String>,
    pub(in crate::tools::sandbox) changed_path_kinds: BTreeMap<String, String>,
    pub(in crate::tools::sandbox) mutation_source: String,
    pub(in crate::tools::sandbox) conflict_reason: Option<String>,
    pub(in crate::tools::sandbox) error: Option<JsonObject>,
}

}

use std::collections::BTreeMap;

use eos_sandbox_port::{ExecCommandResult, SandboxRequestBase};
use eos_types::JsonObject;
use serde::Serialize;
use serde_json::{json, Value};

use crate::ExecutionMetadata;
use crate::ToolError;
use crate::ToolName;
use crate::ToolResult;



use outputs::{CommandToolOutput, MutationOutput};

pub(super) const MAX_READ_FILE_LINES: u32 = 200;
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

/// Absolute paths pass through; relative paths resolve under `workspace_root`.
pub(super) fn resolve_path(ctx: &ExecutionMetadata, path: &str) -> String {
    if path.starts_with('/') {
        return path.to_owned();
    }
    let workspace_root = ctx.workspace_root.trim();
    if workspace_root.is_empty() {
        path.to_owned()
    } else {
        format!("{}/{path}", workspace_root.trim_end_matches('/'))
    }
}

pub(super) fn cwd(ctx: &ExecutionMetadata) -> String {
    ctx.workspace_root.trim().to_owned()
}

pub(super) fn serialize_output<T: Serialize>(value: &T) -> Result<String, ToolResult> {
    serde_json::to_string(value)
        .map_err(|err| ToolResult::error(format!("failed to serialize tool output: {err}")))
}

pub(super) fn ok_json<T: Serialize>(value: &T) -> ToolResult {
    match serialize_output(value) {
        Ok(output) => ToolResult::ok(output),
        Err(result) => result,
    }
}

pub(super) fn invalid_input(tool: ToolName, message: impl std::fmt::Display) -> ToolResult {
    ToolResult::error(format!(
        "Invalid input for {}: {message}. Please retry the tool call with valid arguments.",
        tool.as_str()
    ))
}

pub(super) fn failure_status(conflict_reason: Option<&str>) -> String {
    match conflict_reason {
        Some("base_mismatch" | "version_conflict" | "drift") => "aborted_version",
        Some("lock_conflict" | "locked") => "aborted_lock",
        Some("not_found" | "missing") => "not_found",
        _ => "failed",
    }
    .to_owned()
}

pub(super) fn default_false() -> bool {
    false
}

pub(super) fn default_empty() -> String {
    String::new()
}

pub(super) fn mutation_result(success: bool, output: MutationOutput) -> ToolResult {
    let serialized = match serialize_output(&output) {
        Ok(output) => output,
        Err(result) => return result,
    };
    let mut result = if success {
        ToolResult::ok(serialized)
    } else {
        ToolResult::error(serialized)
    };
    result
        .metadata
        .insert("status".to_owned(), Value::String(output.status));
    result
}

pub(super) fn edit_output(
    ctx: &ExecutionMetadata,
    file_path: String,
    base: &eos_sandbox_port::SandboxResultBase,
    changed_path_kinds: BTreeMap<String, String>,
    mutation_source: String,
    applied_edits: u64,
) -> ToolResult {
    let output = MutationOutput {
        cwd: cwd(ctx),
        file_path,
        status: if base.success {
            "edited".to_owned()
        } else {
            failure_status(base.conflict_reason.as_deref())
        },
        changed_paths: base.changed_paths.clone(),
        changed_path_kinds,
        mutation_source,
        conflict_reason: base.conflict_reason.clone(),
        error: base.error.clone().unwrap_or_default(),
        extra: BTreeMap::from([("applied_edits".to_owned(), json!(applied_edits))]),
    };
    mutation_result(base.success, output)
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

// Isolated-workspace lifecycle tools.

use eos_sandbox_port::{
    EnterIsolatedWorkspaceResult, ExitIsolatedWorkspaceResult, LifecycleError, SandboxPortError,
};


const DEFAULT_LAYER_STACK_ROOT: &str = "/eos/state/layer-stack";

fn effective_layer_stack_root(layer_stack_root: &str) -> String {
    if layer_stack_root.is_empty() {
        DEFAULT_LAYER_STACK_ROOT.to_owned()
    } else {
        layer_stack_root.to_owned()
    }
}

fn render_enter_result(result: &EnterIsolatedWorkspaceResult) -> Result<ToolResult, ToolError> {
    render_lifecycle(
        result.base.success,
        &json!({
            "success": result.base.success,
            "manifest_version": result.manifest_version,
            "manifest_root_hash": result.manifest_root_hash,
            "error": lifecycle_error_value(result.base.error.as_ref()),
        }),
    )
}

fn render_exit_result(result: &ExitIsolatedWorkspaceResult) -> Result<ToolResult, ToolError> {
    render_lifecycle(
        result.base.success,
        &json!({
            "success": result.base.success,
            "evicted_upperdir_bytes": result.evicted_upperdir_bytes,
            "lifetime_s": result.lifetime_s,
            "phases_ms": result.phases_ms,
            "error": lifecycle_error_value(result.base.error.as_ref()),
        }),
    )
}

fn render_enter_api_failure(error: &SandboxPortError) -> Result<ToolResult, ToolError> {
    render_lifecycle(
        false,
        &json!({
            "success": false,
            "manifest_version": "",
            "manifest_root_hash": "",
            "error": lifecycle_error_value(Some(&lifecycle_error_from_api(error))),
        }),
    )
}

fn render_exit_api_failure(error: &SandboxPortError) -> Result<ToolResult, ToolError> {
    render_lifecycle(
        false,
        &json!({
            "success": false,
            "evicted_upperdir_bytes": 0,
            "lifetime_s": 0.0,
            "phases_ms": {},
            "error": lifecycle_error_value(Some(&lifecycle_error_from_api(error))),
        }),
    )
}

fn render_lifecycle(success: bool, payload: &Value) -> Result<ToolResult, ToolError> {
    let output = serde_json::to_string_pretty(payload).map_err(|err| {
        ToolError::Internal(format!("failed to serialize lifecycle result: {err}"))
    })?;
    Ok(if success {
        ToolResult::ok(output)
    } else {
        ToolResult::error(output)
    })
}

fn lifecycle_error_value(error: Option<&LifecycleError>) -> Value {
    match error {
        Some(error) => json!({
            "kind": error.kind,
            "message": error.message,
            "details": error.details,
        }),
        None => Value::Null,
    }
}

fn lifecycle_error_from_api(error: &SandboxPortError) -> LifecycleError {
    let fallback = match error {
        SandboxPortError::Decode { .. } => "decode_error",
        _ => "internal_error",
    };
    LifecycleError {
        kind: error.code().unwrap_or(fallback).to_owned(),
        message: error.message().to_owned(),
        details: BTreeMap::new(),
    }
}

mod read_file {
use async_trait::async_trait;
use eos_sandbox_port::ReadFileRequest;
use eos_types::JsonObject;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::tools::parse_input;
use crate::ExecutionMetadata;
use crate::ToolError;
use crate::ToolExecutor;
use crate::ToolName;
use crate::ToolResult;

use crate::tools::SandboxHandle;
use super::outputs::ReadFileOutput;
use super::{cwd, invalid_input, ok_json, request_base, resolve_path, MAX_READ_FILE_LINES};

fn default_one() -> u32 {
    1
}
fn default_max_read_lines() -> u32 {
    MAX_READ_FILE_LINES
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct ReadFileInput {
    file_path: String,
    #[serde(default = "default_one")]
    #[schemars(default = "default_one", range(min = 1))]
    start_line: u32,
    #[serde(default = "default_max_read_lines")]
    #[schemars(default = "default_max_read_lines", range(min = 1))]
    end_line: u32,
}

pub(super) struct ReadFile {
    service: SandboxHandle,
}

impl ReadFile {
    pub(super) fn new(service: SandboxHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for ReadFile {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: ReadFileInput = match parse_input(ToolName::ReadFile, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        // `default_end_line_to_window`: auto-window only when end_line is omitted.
        let end_line = if input.contains_key("end_line") {
            parsed.end_line
        } else {
            parsed
                .start_line
                .saturating_add(MAX_READ_FILE_LINES.saturating_sub(1))
        };
        if parsed.start_line == 0 {
            return Ok(invalid_input(ToolName::ReadFile, "start_line must be >= 1"));
        }
        if end_line == 0 {
            return Ok(invalid_input(ToolName::ReadFile, "end_line must be >= 1"));
        }
        if end_line < parsed.start_line {
            return Ok(invalid_input(
                ToolName::ReadFile,
                "end_line cannot be smaller than start_line",
            ));
        }
        if end_line - parsed.start_line + 1 > MAX_READ_FILE_LINES {
            return Ok(invalid_input(
                ToolName::ReadFile,
                format!("read_file can return at most {MAX_READ_FILE_LINES} lines"),
            ));
        }

        let path = resolve_path(ctx, &parsed.file_path);
        let sandbox_id = ctx.require_sandbox_id()?;
        let request = ReadFileRequest {
            base: request_base(ctx, &format!("read {path}"))?,
            path: path.clone(),
        };
        let result =
            match eos_sandbox_port::read_file(&*self.service.transport, sandbox_id, &request).await
            {
                Ok(result) => result,
                Err(err) => return Ok(ToolResult::error(err.to_string())),
            };
        if !result.base.success {
            return Ok(ToolResult::error(format!("Failed to read file: {path}")));
        }
        if !result.exists {
            return Ok(ToolResult::error(format!("Path does not exist: {path}")));
        }

        let output =
            build_read_file_output(ctx, &path, &result.content, parsed.start_line, end_line);
        Ok(ok_json(&output))
    }
}

/// `build_read_file_result`: window + `cat -n`-style line numbering done in the
/// tool over the daemon's full-file content.
fn build_read_file_output(
    ctx: &ExecutionMetadata,
    file_path: &str,
    content: &str,
    start_line: u32,
    end_line: u32,
) -> ReadFileOutput {
    let start = start_line.max(1);
    let mut rendered = Vec::new();
    let mut total = 0;
    if !content.is_empty() {
        for (idx, line) in content.split('\n').enumerate() {
            let n = idx as u32 + 1;
            total = n;
            if n >= start && n <= end_line {
                rendered.push(format!("{n:4}: {line}"));
            }
        }
    }
    let end = end_line.min(total);
    ReadFileOutput {
        cwd: cwd(ctx),
        file_path: file_path.to_owned(),
        total_lines: total,
        start_line: start,
        end_line: end,
        content: rendered.join("\n"),
    }
}

}
mod write_file {
use std::collections::BTreeMap;

use async_trait::async_trait;
use eos_sandbox_port::WriteFileRequest;
use eos_types::JsonObject;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::tools::parse_input;
use crate::ExecutionMetadata;
use crate::ToolError;
use crate::ToolExecutor;
use crate::ToolName;
use crate::ToolResult;

use crate::tools::SandboxHandle;
use super::outputs::MutationOutput;
use super::{cwd, failure_status, mutation_result, request_base, resolve_path};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct WriteFileInput {
    file_path: String,
    content: String,
}

pub(super) struct WriteFile {
    service: SandboxHandle,
}

impl WriteFile {
    pub(super) fn new(service: SandboxHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for WriteFile {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: WriteFileInput = match parse_input(ToolName::WriteFile, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        let WriteFileInput { file_path, content } = parsed;
        let bytes = content.len() as u64;
        let path = resolve_path(ctx, &file_path);
        let sandbox_id = ctx.require_sandbox_id()?;
        let request = WriteFileRequest {
            base: request_base(ctx, &format!("write {path}"))?,
            path: path.clone(),
            content,
            overwrite: true,
        };
        let result = match eos_sandbox_port::write_file(
            &*self.service.transport,
            sandbox_id,
            &request,
        )
        .await
        {
            Ok(result) => result,
            Err(err) => return Ok(ToolResult::error(err.to_string())),
        };
        let output = MutationOutput {
            cwd: cwd(ctx),
            file_path: path,
            status: if result.base.success {
                "written".to_owned()
            } else {
                failure_status(result.base.conflict_reason.as_deref())
            },
            changed_paths: result.base.changed_paths,
            changed_path_kinds: result.changed_path_kinds,
            mutation_source: result.mutation_source,
            conflict_reason: result.base.conflict_reason,
            error: result.base.error.unwrap_or_default(),
            extra: BTreeMap::from([("bytes_written".to_owned(), json!(bytes))]),
        };
        Ok(mutation_result(result.base.success, output))
    }
}

}
mod edit_file {
use async_trait::async_trait;
use eos_sandbox_port::{EditFileRequest, SearchReplaceEdit};
use eos_types::JsonObject;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::tools::parse_input;
use crate::ExecutionMetadata;
use crate::ToolError;
use crate::ToolExecutor;
use crate::ToolName;
use crate::ToolResult;

use crate::tools::SandboxHandle;
use super::{default_empty, default_false, edit_output, request_base, resolve_path};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct EditFileInput {
    file_path: String,
    #[serde(default = "default_empty")]
    old_text: String,
    #[serde(default = "default_empty")]
    new_text: String,
    #[serde(default = "default_false")]
    replace_all: bool,
    #[serde(default = "default_empty")]
    description: String,
}

pub(super) struct EditFile {
    service: SandboxHandle,
}

impl EditFile {
    pub(super) fn new(service: SandboxHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for EditFile {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: EditFileInput = match parse_input(ToolName::EditFile, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.old_text.is_empty() {
            return Ok(ToolResult::error(
                "Provide `old_text` (text to find) and `new_text` (replacement).",
            ));
        }
        let path = resolve_path(ctx, &parsed.file_path);
        let sandbox_id = ctx.require_sandbox_id()?;
        let description = if parsed.description.is_empty() {
            format!("edit {path}")
        } else {
            parsed.description.clone()
        };
        let request = EditFileRequest {
            base: request_base(ctx, &description)?,
            path: path.clone(),
            edits: vec![SearchReplaceEdit {
                old_text: parsed.old_text,
                new_text: parsed.new_text,
                replace_all: parsed.replace_all,
            }],
        };
        let result =
            match eos_sandbox_port::edit_file(&*self.service.transport, sandbox_id, &request).await
            {
                Ok(result) => result,
                Err(err) => return Ok(ToolResult::error(err.to_string())),
            };
        let applied = if result.base.success {
            u64::from(result.applied_edits)
        } else {
            0
        };
        Ok(edit_output(
            ctx,
            path,
            &result.base,
            result.changed_path_kinds,
            result.mutation_source,
            applied,
        ))
    }
}

}
mod multi_edit {
use async_trait::async_trait;
use eos_sandbox_port::{EditFileRequest, SearchReplaceEdit};
use eos_types::JsonObject;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::tools::parse_input;
use crate::ExecutionMetadata;
use crate::ToolError;
use crate::ToolExecutor;
use crate::ToolName;
use crate::ToolResult;

use crate::tools::SandboxHandle;
use super::{default_empty, default_false, edit_output, request_base, resolve_path};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct MultiEditOp {
    old_text: String,
    #[serde(default = "default_empty")]
    new_text: String,
    #[serde(default = "default_false")]
    replace_all: bool,
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct MultiEditInput {
    file_path: String,
    edits: Vec<MultiEditOp>,
    #[serde(default = "default_empty")]
    description: String,
}

pub(super) struct MultiEdit {
    service: SandboxHandle,
}

impl MultiEdit {
    pub(super) fn new(service: SandboxHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for MultiEdit {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: MultiEditInput = match parse_input(ToolName::MultiEdit, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        if parsed.edits.is_empty() {
            return Ok(ToolResult::error("Provide at least one edit in `edits`."));
        }
        let path = resolve_path(ctx, &parsed.file_path);
        let sandbox_id = ctx.require_sandbox_id()?;
        let count = parsed.edits.len() as u64;
        let description = if parsed.description.is_empty() {
            format!("multi-edit {path}")
        } else {
            parsed.description.clone()
        };
        let request = EditFileRequest {
            base: request_base(ctx, &description)?,
            path: path.clone(),
            edits: parsed
                .edits
                .into_iter()
                .map(|op| SearchReplaceEdit {
                    old_text: op.old_text,
                    new_text: op.new_text,
                    replace_all: op.replace_all,
                })
                .collect(),
        };
        let result =
            match eos_sandbox_port::edit_file(&*self.service.transport, sandbox_id, &request).await
            {
                Ok(result) => result,
                Err(err) => return Ok(ToolResult::error(err.to_string())),
            };
        // multi_edit reports the count of edits submitted (not result.applied_edits).
        let applied = if result.base.success { count } else { 0 };
        Ok(edit_output(
            ctx,
            path,
            &result.base,
            result.changed_path_kinds,
            result.mutation_source,
            applied,
        ))
    }
}

}
mod enter_isolated_workspace {
//! The `enter_isolated_workspace` lifecycle tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_sandbox_port::{EnterIsolatedWorkspaceRequest, SandboxRequestBase};
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};

use crate::registry::ToolConfigSet;
use crate::registry::text_spec;
use crate::tools::parse_input;
use crate::tools::SandboxHandle;
use crate::ExecutionMetadata;
use crate::ToolError;
use crate::ToolExecutor;
use crate::ToolName;
use crate::ToolRegistry;
use crate::{OutputShape, ToolResult};

use super::{effective_layer_stack_root, render_enter_api_failure, render_enter_result};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct EnterIsolatedWorkspaceInput {
    #[serde(default)]
    layer_stack_root: String,
}

struct EnterIsolatedWorkspace {
    service: SandboxHandle,
}

impl EnterIsolatedWorkspace {
    fn new(service: SandboxHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for EnterIsolatedWorkspace {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: EnterIsolatedWorkspaceInput =
            match parse_input(ToolName::EnterIsolatedWorkspace, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        let sandbox_id = ctx.require_sandbox_id()?;
        let agent_run_id = ctx.require_agent_run_id()?;
        let request = EnterIsolatedWorkspaceRequest {
            base: SandboxRequestBase::new(agent_run_id.as_str(), "enter isolated workspace", None),
            layer_stack_root: effective_layer_stack_root(&parsed.layer_stack_root),
        };
        let result = match eos_sandbox_port::enter_isolated_workspace(
            &*self.service.transport,
            sandbox_id,
            &request,
        )
        .await
        {
            Ok(result) => result,
            Err(err) => return render_enter_api_failure(&err),
        };
        if result.base.success {
            self.service
                .set_isolated_workspace_mode(agent_run_id, true)
                .await?;
        }
        render_enter_result(&result)
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    sandbox_service: SandboxHandle,
) {
    let enter = config.get(ToolName::EnterIsolatedWorkspace);
    crate::tools::register_tool(
        registry,
        ToolName::EnterIsolatedWorkspace,
        enter,
        text_spec(
            ToolName::EnterIsolatedWorkspace,
            &enter.description,
            schema_for!(EnterIsolatedWorkspaceInput),
        ),
        OutputShape::Text,
        Arc::new(EnterIsolatedWorkspace::new(sandbox_service)),
    );
}

}
mod exit_isolated_workspace {
//! The `exit_isolated_workspace` lifecycle tool.

use std::sync::Arc;

use async_trait::async_trait;
use eos_sandbox_port::{ExitIsolatedWorkspaceRequest, SandboxRequestBase};
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};

use crate::registry::ToolConfigSet;
use crate::registry::text_spec;
use crate::tools::parse_input;
use crate::tools::SandboxHandle;
use crate::ExecutionMetadata;
use crate::ToolError;
use crate::ToolExecutor;
use crate::ToolName;
use crate::ToolRegistry;
use crate::{OutputShape, ToolResult};

use super::{render_exit_api_failure, render_exit_result};

fn default_grace_s() -> f64 {
    5.0
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct ExitIsolatedWorkspaceInput {
    #[serde(default = "default_grace_s")]
    #[schemars(default = "default_grace_s")]
    grace_s: f64,
}

struct ExitIsolatedWorkspace {
    service: SandboxHandle,
}

impl ExitIsolatedWorkspace {
    fn new(service: SandboxHandle) -> Self {
        Self { service }
    }
}

#[async_trait]
impl ToolExecutor for ExitIsolatedWorkspace {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: ExitIsolatedWorkspaceInput =
            match parse_input(ToolName::ExitIsolatedWorkspace, input) {
                Ok(v) => v,
                Err(err) => return Ok(err),
            };
        let sandbox_id = ctx.require_sandbox_id()?;
        let agent_run_id = ctx.require_agent_run_id()?;
        let request = ExitIsolatedWorkspaceRequest {
            base: SandboxRequestBase::new(agent_run_id.as_str(), "exit isolated workspace", None),
            grace_s: parsed.grace_s,
        };
        let result = match eos_sandbox_port::exit_isolated_workspace(
            &*self.service.transport,
            sandbox_id,
            &request,
        )
        .await
        {
            Ok(result) => result,
            Err(err) => return render_exit_api_failure(&err),
        };
        if result.base.success {
            self.service
                .set_isolated_workspace_mode(agent_run_id, false)
                .await?;
        }
        render_exit_result(&result)
    }
}

pub(super) fn register(
    registry: &mut ToolRegistry,
    config: &ToolConfigSet,
    sandbox_service: SandboxHandle,
) {
    let exit = config.get(ToolName::ExitIsolatedWorkspace);
    crate::tools::register_tool(
        registry,
        ToolName::ExitIsolatedWorkspace,
        exit,
        text_spec(
            ToolName::ExitIsolatedWorkspace,
            &exit.description,
            schema_for!(ExitIsolatedWorkspaceInput),
        ),
        OutputShape::Text,
        Arc::new(ExitIsolatedWorkspace::new(sandbox_service)),
    );
}

}

pub(crate) fn register(
    registry: &mut crate::ToolRegistry,
    config: &crate::registry::ToolConfigSet,
    sandbox: crate::tools::SandboxHandle,
) {
    use std::sync::Arc;

    use crate::registry::json_spec;
    use crate::{OutputShape, ToolName};
    use outputs::{MutationOutput, ReadFileOutput};
    use schemars::schema_for;

    let read_file = config.get(ToolName::ReadFile);
    crate::tools::register_tool(
        registry,
        ToolName::ReadFile,
        read_file,
        json_spec(
            ToolName::ReadFile,
            &read_file.description,
            schema_for!(read_file::ReadFileInput),
            schema_for!(ReadFileOutput),
        ),
        OutputShape::json::<ReadFileOutput>("ReadFileOutput"),
        Arc::new(read_file::ReadFile::new(sandbox.clone())),
    );
    let write_file = config.get(ToolName::WriteFile);
    crate::tools::register_tool(
        registry,
        ToolName::WriteFile,
        write_file,
        json_spec(
            ToolName::WriteFile,
            &write_file.description,
            schema_for!(write_file::WriteFileInput),
            schema_for!(MutationOutput),
        ),
        OutputShape::json::<MutationOutput>("WriteFileOutput"),
        Arc::new(write_file::WriteFile::new(sandbox.clone())),
    );
    let edit_file = config.get(ToolName::EditFile);
    crate::tools::register_tool(
        registry,
        ToolName::EditFile,
        edit_file,
        json_spec(
            ToolName::EditFile,
            &edit_file.description,
            schema_for!(edit_file::EditFileInput),
            schema_for!(MutationOutput),
        ),
        OutputShape::json::<MutationOutput>("EditFileOutput"),
        Arc::new(edit_file::EditFile::new(sandbox.clone())),
    );
    let multi_edit = config.get(ToolName::MultiEdit);
    crate::tools::register_tool(
        registry,
        ToolName::MultiEdit,
        multi_edit,
        json_spec(
            ToolName::MultiEdit,
            &multi_edit.description,
            schema_for!(multi_edit::MultiEditInput),
            schema_for!(MutationOutput),
        ),
        OutputShape::json::<MutationOutput>("MultiEditOutput"),
        Arc::new(multi_edit::MultiEdit::new(sandbox.clone())),
    );
    enter_isolated_workspace::register(registry, config, sandbox.clone());
    exit_isolated_workspace::register(registry, config, sandbox);
}
