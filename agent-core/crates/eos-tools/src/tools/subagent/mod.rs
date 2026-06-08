//! Subagent tools.

mod cancel_subagent;
mod check_subagent_progress;
mod lib;
mod run_subagent;

use std::sync::Arc;

use super::CallerScope;
use crate::ports::{AgentRunServicePort, SubagentSessionPort};

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    caller: &CallerScope,
    agent_run_service: Option<Arc<dyn AgentRunServicePort>>,
    subagent_sessions: Option<Arc<dyn SubagentSessionPort>>,
) {
    run_subagent::register(
        registry,
        config,
        caller,
        agent_run_service,
        subagent_sessions.clone(),
    );
    check_subagent_progress::register(registry, config, subagent_sessions.clone());
    cancel_subagent::register(registry, config, subagent_sessions);
}
