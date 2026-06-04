use async_trait::async_trait;
use eos_sandbox_api::{GrepOutputMode, GrepRequest};
use eos_types::JsonObject;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
use crate::core::result::ToolResult;
use crate::runtime::execution::parse_input;
use crate::runtime::executor::ToolExecutor;

use super::lib::outputs::GrepOutput;
use super::lib::{cwd, ok_json, request_base, resolve_path};

fn default_false() -> bool {
    false
}

fn default_grep_mode() -> GrepOutputMode {
    GrepOutputMode::FilesWithMatches
}
fn default_head_limit() -> u32 {
    250
}
fn default_zero() -> u32 {
    0
}

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct GrepInput {
    pattern: String,
    #[serde(default)]
    path: Option<String>,
    #[serde(default)]
    glob_filter: Option<String>,
    #[serde(default = "default_grep_mode")]
    #[schemars(default = "default_grep_mode")]
    output_mode: GrepOutputMode,
    #[serde(default = "default_head_limit")]
    #[schemars(default = "default_head_limit")]
    head_limit: u32,
    #[serde(default = "default_zero")]
    offset: u32,
    #[serde(default = "default_false")]
    case_insensitive: bool,
    #[serde(default = "default_false")]
    line_numbers: bool,
    #[serde(default = "default_false")]
    multiline: bool,
}

pub(super) struct Grep;

#[async_trait]
impl ToolExecutor for Grep {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: GrepInput = match parse_input(ToolName::Grep, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        let resolved = parsed.path.as_deref().map(|p| resolve_path(ctx, p));
        let sandbox_id = ctx.require_sandbox_id()?;
        let request = GrepRequest {
            base: request_base(ctx, "grep"),
            pattern: parsed.pattern.clone(),
            path: resolved,
            glob_filter: parsed.glob_filter,
            output_mode: parsed.output_mode,
            head_limit: Some(parsed.head_limit),
            offset: parsed.offset,
            case_insensitive: parsed.case_insensitive,
            line_numbers: parsed.line_numbers,
            multiline: parsed.multiline,
        };
        let result = match eos_sandbox_api::grep(&*ctx.transport, sandbox_id, &request).await {
            Ok(result) => result,
            Err(err) => return Ok(ToolResult::error(err.to_string())),
        };
        if !result.base.success {
            return Ok(ToolResult::error(format!(
                "grep failed for pattern: {}",
                parsed.pattern
            )));
        }
        let output = GrepOutput {
            cwd: cwd(ctx),
            pattern: parsed.pattern,
            mode: result.output_mode,
            filenames: result.filenames,
            content: result.content,
            num_files: result.num_files,
            num_lines: result.num_lines,
            num_matches: result.num_matches,
            applied_limit: result.applied_limit,
            applied_offset: result.applied_offset,
            truncated: result.truncated,
        };
        Ok(ok_json(&output))
    }
}
