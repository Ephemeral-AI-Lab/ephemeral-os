//! Passive background-session status DTOs.

/// Per-kind in-flight background-session count for one agent run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub struct BackgroundSessionCounts {
    /// `subagents + workflows + command_sessions`.
    pub total: usize,
    /// In-flight subagent runs for this agent run.
    pub subagents: usize,
    /// Outstanding delegated workflows for this agent run.
    pub workflows: usize,
    /// In-flight background-tracked command sessions for this agent run.
    pub command_sessions: usize,
}
