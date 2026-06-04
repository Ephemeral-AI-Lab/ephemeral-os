use eos_sandbox_api::SandboxRequestBase;
use serde::Serialize;

use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::result::ToolResult;

pub(super) const MAX_READ_FILE_LINES: u32 = 200;
pub(super) const MAX_YIELD_TIME_MS: u32 = 30_000;

// ---------------------------------------------------------------------------
// Shared helpers (ported from sandbox/_lib/tool_context.py).
// ---------------------------------------------------------------------------

pub(super) fn request_base(ctx: &ExecutionMetadata, description: &str) -> SandboxRequestBase {
    SandboxRequestBase {
        caller: ctx.caller.clone(),
        description: description.to_owned(),
        invocation_id: ctx.sandbox_invocation_id.clone(),
    }
}

/// `resolve_tool_sandbox_path`: absolute paths pass through; otherwise join under
/// `repo_root`.
pub(super) fn resolve_path(ctx: &ExecutionMetadata, path: &str) -> String {
    if path.starts_with('/') {
        return path.to_owned();
    }
    let repo_root = ctx.repo_root.trim();
    if repo_root.is_empty() {
        path.to_owned()
    } else {
        format!("{}/{path}", repo_root.trim_end_matches('/'))
    }
}

pub(super) fn cwd(ctx: &ExecutionMetadata) -> String {
    ctx.repo_root.trim().to_owned()
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

/// `_failure_status(conflict_reason)`.
pub(super) fn failure_status(conflict_reason: Option<&str>) -> String {
    match conflict_reason {
        Some("base_mismatch" | "version_conflict" | "drift") => "aborted_version",
        Some("lock_conflict" | "locked") => "aborted_lock",
        Some("not_found" | "missing") => "not_found",
        _ => "failed",
    }
    .to_owned()
}
