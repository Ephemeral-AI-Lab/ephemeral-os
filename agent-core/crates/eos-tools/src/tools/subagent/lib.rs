use eos_tool_ports::ToolName;
use eos_tool_ports::ToolResult;

#[cfg(test)]
#[path = "../../../tests/tools/subagent/mod.rs"]
mod tests;

pub(super) fn empty_subagent_agent_run_error(tool: ToolName) -> ToolResult {
    ToolResult::error(format!(
        "Invalid input for {}: agent_run_id must be non-empty. \
         Please retry the tool call with valid arguments.",
        tool.as_str()
    ))
}
