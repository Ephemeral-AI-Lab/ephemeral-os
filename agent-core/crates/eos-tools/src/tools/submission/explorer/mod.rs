mod submit_exploration_result;

pub(super) fn register(
    registry: &mut eos_tool_ports::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
) {
    submit_exploration_result::register(registry, config);
}
