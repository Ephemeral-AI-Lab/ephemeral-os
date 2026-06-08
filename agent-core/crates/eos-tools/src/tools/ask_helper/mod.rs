//! Ask-helper tools.

mod advisor_prompt;
mod ask_advisor;

use std::sync::Arc;

use crate::AgentRunServicePort;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    agent_run_service: Option<Arc<dyn AgentRunServicePort>>,
) {
    ask_advisor::register(registry, config, agent_run_service);
}
