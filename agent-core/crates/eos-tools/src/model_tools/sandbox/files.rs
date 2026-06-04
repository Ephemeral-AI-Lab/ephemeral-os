use std::collections::BTreeMap;

use async_trait::async_trait;
use eos_sandbox_api::{EditFileRequest, ReadFileRequest, SearchReplaceEdit, WriteFileRequest};
use eos_types::JsonObject;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::error::ToolError;
use crate::execution::parse_input;
use crate::executor::ToolExecutor;
use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::result::ToolResult;

use super::common::{
    cwd, failure_status, invalid_input, ok_json, request_base, resolve_path, serialize_output,
    MAX_READ_FILE_LINES,
};
use super::outputs::{MutationOutput, ReadFileOutput};

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

pub(super) struct ReadFile;

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
            base: request_base(ctx, &format!("read {path}")),
            path: path.clone(),
        };
        let result = match eos_sandbox_api::read_file(&*ctx.transport, sandbox_id, &request).await {
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
    let lines: Vec<&str> = if content.is_empty() {
        Vec::new()
    } else {
        content.split('\n').collect()
    };
    let total = lines.len() as u32;
    let start = start_line.max(1);
    let end = end_line.min(total);
    let mut rendered = Vec::new();
    if total > 0 && start <= end {
        for n in start..=end {
            rendered.push(format!("{n:4}: {}", lines[(n - 1) as usize]));
        }
    }
    ReadFileOutput {
        cwd: cwd(ctx),
        file_path: file_path.to_owned(),
        total_lines: total,
        start_line: start,
        end_line: end,
        content: rendered.join("\n"),
    }
}

// ---------------------------------------------------------------------------
// write_file
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct WriteFileInput {
    file_path: String,
    content: String,
}

pub(super) struct WriteFile;

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
        let path = resolve_path(ctx, &parsed.file_path);
        let sandbox_id = ctx.require_sandbox_id()?;
        let request = WriteFileRequest {
            base: request_base(ctx, &format!("write {path}")),
            path: path.clone(),
            content: parsed.content.clone(),
            overwrite: true,
        };
        let result = match eos_sandbox_api::write_file(&*ctx.transport, sandbox_id, &request).await
        {
            Ok(result) => result,
            Err(err) => return Ok(ToolResult::error(err.to_string())),
        };
        let bytes = parsed.content.len() as u64;
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

// ---------------------------------------------------------------------------
// edit_file + multi_edit
// ---------------------------------------------------------------------------

fn default_false() -> bool {
    false
}
fn default_empty() -> String {
    String::new()
}

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

pub(super) struct EditFile;

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
            base: request_base(ctx, &description),
            path: path.clone(),
            edits: vec![SearchReplaceEdit {
                old_text: parsed.old_text,
                new_text: parsed.new_text,
                replace_all: parsed.replace_all,
            }],
        };
        let result = match eos_sandbox_api::edit_file(&*ctx.transport, sandbox_id, &request).await {
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

pub(super) struct MultiEdit;

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
            base: request_base(ctx, &description),
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
        let result = match eos_sandbox_api::edit_file(&*ctx.transport, sandbox_id, &request).await {
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

fn edit_output(
    ctx: &ExecutionMetadata,
    file_path: String,
    base: &eos_sandbox_api::SandboxResultBase,
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

fn mutation_result(success: bool, output: MutationOutput) -> ToolResult {
    let serialized = match serialize_output(&output) {
        Ok(output) => output,
        Err(result) => return result,
    };
    let mut result = if success {
        ToolResult::ok(serialized)
    } else {
        ToolResult::error(serialized)
    };
    // Moves `output.status` out, consuming `output` (so it is not a
    // pass-by-reference-only argument).
    result
        .metadata
        .insert("status".to_owned(), Value::String(output.status));
    result
}
