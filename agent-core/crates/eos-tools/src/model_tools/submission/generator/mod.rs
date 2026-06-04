mod submit_generator_outcome;

pub(super) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::config::ToolConfigSet,
) {
    submit_generator_outcome::register(registry, config);
}
