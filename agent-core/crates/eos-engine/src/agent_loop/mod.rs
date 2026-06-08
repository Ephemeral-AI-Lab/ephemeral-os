//! Public non-blocking agent-loop API and internal loop executor.

mod agent_loop_executor;
mod agent_loop_state;
mod contracts;
mod launcher;
mod loop_hooks;

pub use contracts::{
    AgentLoopBackgroundDependencies, AgentLoopToolRegistryBuildInput, AgentLoopToolRegistryFactory,
};
pub use launcher::{start_agent_loop, TokioAgentLoopLauncher};

pub(crate) use agent_loop_executor::AgentLoopExecutor;
pub(crate) use agent_loop_state::{AgentLoopRunServices, AgentLoopState};
pub(crate) use launcher::AgentLoopEventSource;
pub(crate) use loop_hooks::{AgentLoopHooks, NoopAgentLoopHooks};
