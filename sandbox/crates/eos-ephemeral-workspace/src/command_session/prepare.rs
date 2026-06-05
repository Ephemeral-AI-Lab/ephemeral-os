use eos_workspace_api::{PrepareCommandRequest, PreparedCommandWorkspace, WorkspaceApiError};

use super::types::EphemeralCommandSessionPort;

pub(super) fn prepare_command_workspace<P>(
    port: &P,
    request: PrepareCommandRequest,
) -> Result<PreparedCommandWorkspace, WorkspaceApiError>
where
    P: EphemeralCommandSessionPort,
{
    port.prepare_ephemeral_command_workspace(request)
}
