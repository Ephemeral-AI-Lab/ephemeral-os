use eos_workspace_api::{PrepareCommandRequest, PreparedCommandWorkspace, WorkspaceApiError};

use super::types::IsolatedCommandSessionPort;

pub(super) fn prepare_command_workspace<P>(
    port: &P,
    request: PrepareCommandRequest,
) -> Result<PreparedCommandWorkspace, WorkspaceApiError>
where
    P: IsolatedCommandSessionPort,
{
    port.prepare_isolated_command_workspace(request)
}
