use async_trait::async_trait;
use eos_sandbox_api::GlobRequest;
use eos_types::JsonObject;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::core::error::ToolError;
use crate::core::metadata::ExecutionMetadata;
use crate::core::name::ToolName;
use crate::core::result::ToolResult;
use crate::runtime::execution::parse_input;
use crate::runtime::executor::ToolExecutor;

use super::lib::outputs::GlobOutput;
use super::lib::{cwd, ok_json, request_base, resolve_path};

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
pub(super) struct GlobInput {
    pattern: String,
    #[serde(default)]
    path: Option<String>,
}

pub(super) struct Glob;

#[async_trait]
impl ToolExecutor for Glob {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: GlobInput = match parse_input(ToolName::Glob, input) {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        let resolved = parsed.path.as_deref().map(|p| resolve_path(ctx, p));
        let sandbox_id = ctx.require_sandbox_id()?;
        let request = GlobRequest {
            base: request_base(ctx, "glob"),
            pattern: parsed.pattern.clone(),
            path: resolved,
        };
        let result = match eos_sandbox_api::glob(&*ctx.transport, sandbox_id, &request).await {
            Ok(result) => result,
            Err(err) => return Ok(ToolResult::error(err.to_string())),
        };
        if !result.base.success {
            return Ok(ToolResult::error(format!(
                "glob failed for pattern: {}",
                parsed.pattern
            )));
        }
        let output = GlobOutput {
            cwd: cwd(ctx),
            pattern: parsed.pattern,
            filenames: result.filenames,
            num_files: result.num_files,
            truncated: result.truncated,
        };
        Ok(ok_json(&output))
    }
}
