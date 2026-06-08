//! Agent-run transition contracts used by model-facing tools.

use serde::Serialize;

/// Typed launch rejection facts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SubagentLaunchRejection {
    /// The caller is already a subagent.
    Recursive,
    /// The requested agent name is not registered.
    NotRegistered {
        /// Requested agent name.
        agent_name: String,
    },
    /// The requested agent exists but is not subagent-typed.
    NotSubagent {
        /// Requested agent name.
        agent_name: String,
        /// Registered agent type string.
        agent_type: String,
    },
}

/// Terminal background status facts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubagentSessionStatus {
    /// The subagent is still running.
    Running,
    /// The subagent called its terminal tool.
    Completed,
    /// The subagent crashed or exited without terminal output.
    Failed,
    /// The subagent was cancelled.
    Cancelled,
    /// The subagent result was already delivered.
    Delivered,
}

/// Per-kind in-flight background-session count for one agent run.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
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
