//! Runtime sweep operations used by daemon background loops.

#![forbid(unsafe_code)]

use crate::WorkspaceRuntime;

/// Result of one isolated-workspace TTL sweep.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkspaceTtlSweep {
    pub evicted_callers: usize,
}

/// Evict idle isolated workspaces whose TTL has elapsed.
#[must_use]
pub fn sweep_workspace_ttl(workspace: &WorkspaceRuntime) -> WorkspaceTtlSweep {
    WorkspaceTtlSweep {
        evicted_callers: workspace.ttl_sweep(),
    }
}

/// Finalize timed-out or exited command sessions.
pub fn sweep_command_sessions() {
    eos_command_ops::runtime::command_session_reaper_sweep();
}

/// Recover stale command-session metadata left by a prior daemon.
pub fn recover_orphaned_command_sessions() {
    eos_command_ops::runtime::recover_orphaned_command_sessions();
}
