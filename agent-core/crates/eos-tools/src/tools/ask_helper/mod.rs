//! Ask-helper tools.

mod advisor_prompt;
mod ask_advisor;

use std::sync::Arc;

use eos_agent_ports::AgentRunApi;

pub(crate) fn register(
    registry: &mut eos_tool_ports::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    agent_run_service: Option<Arc<dyn AgentRunApi>>,
) {
    ask_advisor::register(registry, config, agent_run_service);
}
