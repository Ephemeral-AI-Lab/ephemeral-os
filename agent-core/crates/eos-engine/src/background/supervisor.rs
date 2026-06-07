//! [`BackgroundTaskSupervisor`] — the per-agent-run ledger for the two
//! lock-coupled lanes ([`SubagentLane`] + [`WorkflowLane`]). It is held behind
//! one `Mutex` so a subagent `spawn`+register and the driver's later settle stay
//! ordered. The command-session lane is **not** here: it is a sibling on
//! [`BackgroundSupervisorHandle`](super::BackgroundSupervisorHandle) with its own
//! interior lock and heartbeat (spec §8.5/§9.3), so the heartbeat never acquires
//! this supervisor lock.
//!
//! There is no record-level `agent_run_id` and no agent-run filter parameter: the
//! owning run is `BackgroundSupervisorRuntime::owner_agent_run_id` (spec §8.5).

use super::lanes::{SubagentLane, WorkflowLane};

/// Single-owner background ledger for one agent run's subagents and workflows.
#[derive(Debug, Default)]
pub struct BackgroundTaskSupervisor {
    /// Subagent lane (local id mint + records + abort backstop).
    pub(super) subagents: SubagentLane,
    /// Delegated-workflow lane (handle + status records).
    pub(super) workflows: WorkflowLane,
}

impl BackgroundTaskSupervisor {
    /// Create an empty supervisor.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }
}
