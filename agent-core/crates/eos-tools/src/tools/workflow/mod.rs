//! Workflow delegation tools.

mod cancel_workflow;
mod check_workflow_status;
mod delegate_workflow;
mod lib;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
) {
    delegate_workflow::register(registry, config);
    check_workflow_status::register(registry, config);
    cancel_workflow::register(registry, config);
}
