use crate::core::name::ToolName;
use crate::core::result::ToolResult;

#[cfg(test)]
#[path = "../../../../tests/tools/workflow/mod.rs"]
mod tests;

pub(super) fn empty_workflow_id_error(tool: ToolName, field: &str) -> ToolResult {
    ToolResult::error(format!(
        "Invalid input for {}: {field} must be non-empty. \
         Please retry the tool call with valid arguments.",
        tool.as_str()
    ))
}
