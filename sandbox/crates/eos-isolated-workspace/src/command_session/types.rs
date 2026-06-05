use eos_workspace_api::{
    FinalizeCommandRequest, PrepareCommandRequest, PreparedCommandWorkspace, WorkspaceApiError,
    WorkspaceCommandOutcome,
};

/// Daemon-supplied port for isolated command-session prepare/finalize policy.
///
/// This port exposes no publish capability. It exists so isolated command
/// workspace policy compiles against `CommandWorkspaceOps` while daemon PTY,
/// child process, registry, and reaper control remain in `eos-daemon`.
pub trait IsolatedCommandSessionPort {
    fn prepare_isolated_command_workspace(
        &self,
        request: PrepareCommandRequest,
    ) -> Result<PreparedCommandWorkspace, WorkspaceApiError>;

    fn finalize_isolated_command_workspace(
        &self,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError>;
}
