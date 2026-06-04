mod submit_planner_outcome;

pub(super) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
) {
    submit_planner_outcome::register(registry, config);
}
