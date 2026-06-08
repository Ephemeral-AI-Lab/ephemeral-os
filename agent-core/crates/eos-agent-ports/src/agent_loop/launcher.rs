//! Agent-loop launcher contract.

use tokio::sync::oneshot;

use super::{AgentLoopOutcome, StartAgentLoopRequest};

/// Public non-blocking launcher for agent loops.
pub trait AgentLoopLauncher: Send + Sync {
    /// Start an agent loop and return immediately with its outcome receiver.
    fn start_agent_loop(&self, request: StartAgentLoopRequest) -> StartedAgentLoop;
}

/// Handle returned after an agent loop has been started.
#[derive(Debug)]
pub struct StartedAgentLoop {
    /// Receives the terminal loop outcome.
    pub outcome_receiver: oneshot::Receiver<AgentLoopOutcome>,
}
