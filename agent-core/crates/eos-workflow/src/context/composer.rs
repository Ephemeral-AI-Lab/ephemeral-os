use eos_tool::{render_tool_instruction, ToolInstructions, ToolName};
use eos_types::AgentDefinition;

pub(crate) fn wrap_task_guidance(prose: &str, agent_def: &AgentDefinition) -> String {
    let body = prose.trim_end();
    if let Some(block) = terminal_selection_block(agent_def) {
        format!("<Assignment Guidance>\n{body}\n\n{block}\n</Assignment Guidance>")
    } else {
        format!("<Assignment Guidance>\n{body}\n</Assignment Guidance>")
    }
}

fn terminal_selection_block(agent_def: &AgentDefinition) -> Option<String> {
    let terminals = agent_def
        .terminals
        .iter()
        .filter_map(|terminal| terminal.parse::<ToolName>().ok())
        .collect::<Vec<_>>();
    if terminals.is_empty() {
        None
    } else {
        let catalog = render_tool_instruction(&terminals, ToolInstructions::SelectionGuidance);
        Some(format!(
            "<terminal_tool_selection>\n{catalog}\n</terminal_tool_selection>"
        ))
    }
}
