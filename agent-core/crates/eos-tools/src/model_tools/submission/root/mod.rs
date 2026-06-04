mod submit_root_outcome;

pub(super) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::config::ToolConfigSet,
) {
    submit_root_outcome::register(registry, config);
}
