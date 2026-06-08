//! Workflow delegation tools.

mod cancel_workflow;
mod check_workflow_status;
mod delegate_workflow;
mod lib;

use std::sync::Arc;

use eos_types::WorkflowApi;

use crate::WorkflowToolService;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    workflow_service: Option<Arc<dyn WorkflowApi>>,
    workflow_sessions: Option<WorkflowToolService>,
) {
    delegate_workflow::register(
        registry,
        config,
        workflow_service.clone(),
        workflow_sessions,
    );
    check_workflow_status::register(registry, config, workflow_service.clone());
    cancel_workflow::register(registry, config, workflow_service);
}
