//! Ask-helper tools.

mod ask_advisor;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
) {
    ask_advisor::register(registry, config);
}
