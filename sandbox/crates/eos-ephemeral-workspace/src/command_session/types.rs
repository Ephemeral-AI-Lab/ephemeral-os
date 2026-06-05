use eos_workspace_api::{
    FinalizeCommandRequest, PrepareCommandRequest, PreparedCommandWorkspace, WorkspaceApiError,
    WorkspaceCommandOutcome,
};

/// Daemon-supplied port for ephemeral command-session prepare/finalize policy.
///
/// The port keeps PTY/process/session registry ownership in `eos-daemon` while
/// allowing this crate to compile against the shared `CommandWorkspaceOps`
/// contract.
pub trait EphemeralCommandSessionPort {
    fn prepare_ephemeral_command_workspace(
        &self,
        request: PrepareCommandRequest,
    ) -> Result<PreparedCommandWorkspace, WorkspaceApiError>;

    fn finalize_ephemeral_command_workspace(
        &self,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError>;
}
