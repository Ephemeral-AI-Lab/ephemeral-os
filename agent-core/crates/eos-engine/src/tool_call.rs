//! Post-message tool dispatch.

pub(crate) mod batch;
pub(crate) mod execute;
mod hooks;

pub(crate) use batch::{lifecycle_batch_decision, reject_terminal_batch, DispatchCall};
pub(crate) use execute::execute_tool_once;
pub(crate) use hooks::ToolCallHooks;
