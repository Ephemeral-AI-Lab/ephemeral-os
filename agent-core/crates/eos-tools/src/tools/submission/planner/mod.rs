mod submit_planner_outcome;

pub(super) fn register(
    registry: &mut eos_tool_ports::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    attempt_submission: Option<super::super::AttemptSubmissionService>,
) {
    submit_planner_outcome::register(registry, config, attempt_submission);
}
