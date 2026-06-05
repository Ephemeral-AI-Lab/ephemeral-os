use eos_workspace_api::{FinalizeCommandRequest, WorkspaceApiError, WorkspaceCommandOutcome};

use super::types::EphemeralCommandSessionPort;

pub(super) fn finalize_command_workspace<P>(
    port: &P,
    request: FinalizeCommandRequest,
) -> Result<WorkspaceCommandOutcome, WorkspaceApiError>
where
    P: EphemeralCommandSessionPort,
{
    port.finalize_ephemeral_command_workspace(request)
}
