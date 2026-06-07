//! The three per-agent-run background lanes (spec §9): [`SubagentLane`],
//! [`WorkflowLane`], and [`CommandSessionLane`]. Each lane owns its records as
//! `handle + status + metadata` (no record-level `agent_run_id` — the owning run
//! is `BackgroundSupervisorRuntime::owner_agent_run_id`), and the command lane
//! additionally owns its own [`CommandCompletionHeartbeat`].

mod command_session;
mod subagent;
mod workflow;

pub use command_session::{CommandSessionHandle, CommandSessionLane, CommandSessionRecord};
pub use subagent::{SubagentHandle, SubagentLane, SubagentRecord};
pub use workflow::{WorkflowBackgroundRecord, WorkflowHandle, WorkflowLane};

/// Background task status, shared by every lane record (spec §8.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackgroundTaskStatus {
    /// Task is running.
    Running,
    /// Task completed normally.
    Completed,
    /// Task failed.
    Failed,
    /// Task was cancelled.
    Cancelled,
    /// Result was delivered to the model.
    Delivered,
}

impl BackgroundTaskStatus {
    /// Terminal precedence; higher status wins when cancel/finish events race.
    #[must_use]
    pub const fn precedence(self) -> u8 {
        match self {
            Self::Running => 0,
            Self::Cancelled => 1,
            Self::Failed => 2,
            Self::Completed => 3,
            Self::Delivered => 4,
        }
    }
}
