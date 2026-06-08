//! Workflow delegation tools.

mod cancel_workflow;
mod check_workflow_status;
mod delegate_workflow;
mod lib;

use std::sync::Arc;

use crate::{WorkflowServicePort, WorkflowToolService};

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    workflow_service: Option<Arc<dyn WorkflowServicePort>>,
    workflow_sessions: Option<WorkflowToolService>,
) {
    delegate_workflow::register(
        registry,
        config,
        workflow_service.clone(),
        workflow_sessions.clone(),
    );
    check_workflow_status::register(registry, config, workflow_service.clone());
    cancel_workflow::register(registry, config, workflow_service);
}
