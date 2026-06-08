//! Agent port errors.

/// Generic agent-port failure.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum AgentPortError {
    /// A store or framework operation failed.
    #[error("agent port operation failed: {0}")]
    Internal(String),
}

pub use eos_types::AgentRunError;
