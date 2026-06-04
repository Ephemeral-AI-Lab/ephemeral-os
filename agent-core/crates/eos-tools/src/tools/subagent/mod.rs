//! Subagent tools.

mod cancel_subagent;
mod check_subagent_progress;
mod lib;
mod run_subagent;

use super::CallerScope;

pub(crate) fn register(
    registry: &mut crate::registry::ToolRegistry,
    config: &crate::registry::config::ToolConfigSet,
    caller: &CallerScope,
) {
    run_subagent::register(registry, config, caller);
    check_subagent_progress::register(registry, config);
    cancel_subagent::register(registry, config);
}
