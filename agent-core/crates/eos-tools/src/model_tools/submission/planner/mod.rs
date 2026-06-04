mod submit_planner_outcome;

pub(super) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::config::ToolConfigSet,
) {
    submit_planner_outcome::register(registry, config);
}
