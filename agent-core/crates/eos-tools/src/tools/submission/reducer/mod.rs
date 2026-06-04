mod submit_reducer_outcome;

pub(super) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
) {
    submit_reducer_outcome::register(registry, config);
}
