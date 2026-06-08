//! Agent-loop launcher and outcome contracts.

mod contracts;
mod launcher;

pub use contracts::{AgentLoopMessage, AgentLoopOutcome, AgentLoopOutcomeKind, StartAgentLoopRequest};
pub use launcher::{AgentLoopLauncher, StartedAgentLoop};
