mod submit_subagent_result;

pub(super) fn register(
    registry: &mut eos_tool_ports::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
) {
    submit_subagent_result::register(registry, config);
}
