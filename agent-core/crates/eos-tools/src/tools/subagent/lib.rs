use crate::core::name::ToolName;
use crate::core::result::ToolResult;

#[cfg(test)]
#[path = "../../../tests/tools/subagent/mod.rs"]
mod tests;

pub(super) fn default_five() -> u8 {
    5
}

pub(super) fn empty_subagent_session_error(tool: ToolName) -> ToolResult {
    ToolResult::error(format!(
        "Invalid input for {}: subagent_session_id must be non-empty. \
         Please retry the tool call with valid arguments.",
        tool.as_str()
    ))
}
