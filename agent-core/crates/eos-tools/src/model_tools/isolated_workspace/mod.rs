//! Isolated-workspace lifecycle tools.

mod enter_isolated_workspace;
mod exit_isolated_workspace;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::config::ToolConfigSet,
) {
    enter_isolated_workspace::register(registry, config);
    exit_isolated_workspace::register(registry, config);
}
