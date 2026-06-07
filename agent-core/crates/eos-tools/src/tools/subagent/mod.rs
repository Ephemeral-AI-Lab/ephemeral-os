//! Subagent tools.

mod cancel_subagent;
mod check_subagent_progress;
mod lib;
mod run_subagent;

use std::sync::Arc;

use super::CallerScope;
use crate::ports::BackgroundSessionPort;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    caller: &CallerScope,
    background_session: Option<Arc<dyn BackgroundSessionPort>>,
) {
    run_subagent::register(registry, config, caller, background_session.clone());
    check_subagent_progress::register(registry, config, background_session.clone());
    cancel_subagent::register(registry, config, background_session);
}
