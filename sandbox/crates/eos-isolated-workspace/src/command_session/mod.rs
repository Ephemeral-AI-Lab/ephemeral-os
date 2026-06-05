//! Command workspace policy boundary for isolated command sessions.

mod finalize;
mod prepare;
pub mod types;

use eos_workspace_api::{
    CommandWorkspaceOps, FinalizeCommandRequest, PrepareCommandRequest, PreparedCommandWorkspace,
    WorkspaceApiError, WorkspaceCommandOutcome,
};

use crate::ops::IsolatedWorkspaceOps;

impl<P> CommandWorkspaceOps for IsolatedWorkspaceOps<P>
where
    P: types::IsolatedCommandSessionPort,
{
    fn prepare_command_workspace(
        &self,
        request: PrepareCommandRequest,
    ) -> Result<PreparedCommandWorkspace, WorkspaceApiError> {
        prepare::prepare_command_workspace(self.ports(), request)
    }

    fn finalize_command_workspace(
        &self,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
        finalize::finalize_command_workspace(self.ports(), request)
    }
}
