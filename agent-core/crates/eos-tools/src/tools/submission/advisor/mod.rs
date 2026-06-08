mod submit_advisor_feedback;

pub(super) fn register(
    registry: &mut eos_tool_ports::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
) {
    submit_advisor_feedback::register(registry, config);
}
