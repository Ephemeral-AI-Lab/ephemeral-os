//! Submission terminal tools.

mod advisor;
mod generator;
mod lib;
mod planner;
mod reducer;
mod root;
mod subagent;

pub(crate) fn register(
    registry: &mut eos_tool_ports::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    root_submission: Option<super::RootSubmissionService>,
    attempt_submission: Option<super::AttemptSubmissionService>,
) {
    planner::register(registry, config, attempt_submission.clone());
    root::register(registry, config, root_submission);
    generator::register(registry, config, attempt_submission.clone());
    reducer::register(registry, config, attempt_submission);
    advisor::register(registry, config);
    subagent::register(registry, config);
}
