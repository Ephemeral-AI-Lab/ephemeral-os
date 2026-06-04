//! Submission terminal tools.

mod advisor;
mod explorer;
mod generator;
mod lib;
mod planner;
mod reducer;
mod root;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
) {
    planner::register(registry, config);
    root::register(registry, config);
    generator::register(registry, config);
    reducer::register(registry, config);
    advisor::register(registry, config);
    explorer::register(registry, config);
}
