//! Agent-run lifecycle adapter.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod active_agent_runs;
mod persistence;
mod service;
mod spawn;

pub use active_agent_runs::ActiveAgentRunRegistry;
pub use service::AgentRunService;
pub use eos_types::{
    AgentRunApi, AgentRunError, AgentRunOutcome, AgentRunStatus, SpawnAgentRequest,
    TaskAgentRunKind, WorkflowTaskRole,
};
