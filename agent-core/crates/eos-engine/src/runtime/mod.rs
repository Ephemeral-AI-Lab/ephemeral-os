mod agent_loop;
mod agent_run_service;
mod cancel;
mod control;
mod factory;
mod foreground;
mod persistence;
mod registry;
mod setup;
mod types;

pub use agent_loop::run_agent;
pub use agent_run_service::{AgentRunService, AgentRunServiceOptions};
pub use cancel::EngineCancelPort;
pub use control::{AgentRunCancellation, AgentRunControl, AgentRunFinalization};
pub use factory::AgentRunControlFactory;
pub use foreground::{ForegroundExecutor, ForegroundExecutorFactory, ForegroundResourceId};
pub use registry::AgentRunRegistry;
pub use setup::build_agent_tool_registry;
pub use types::{
    AgentRunInput, AgentRunResult, AgentToolRegistryServices, EngineRunHandles, EventCallback,
    EventSourceFactory, ToolRegistryExtender,
};
