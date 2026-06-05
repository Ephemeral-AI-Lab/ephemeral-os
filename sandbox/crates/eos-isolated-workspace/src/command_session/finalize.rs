use eos_workspace_api::{FinalizeCommandRequest, WorkspaceApiError, WorkspaceCommandOutcome};

use super::types::IsolatedCommandSessionPort;

pub(super) fn finalize_command_workspace<P>(
    port: &P,
    request: FinalizeCommandRequest,
) -> Result<WorkspaceCommandOutcome, WorkspaceApiError>
where
    P: IsolatedCommandSessionPort,
{
    port.finalize_isolated_command_workspace(request)
}
