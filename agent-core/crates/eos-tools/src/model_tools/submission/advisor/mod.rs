mod submit_advisor_feedback;

pub(super) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::config::ToolConfigSet,
) {
    submit_advisor_feedback::register(registry, config);
}
