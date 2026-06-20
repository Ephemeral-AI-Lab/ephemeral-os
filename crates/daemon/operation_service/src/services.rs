use std::sync::Arc;

use crate::command::CommandOperationService;
use crate::workspace_remount::WorkspaceRemountService;
use crate::workspace_session::WorkspaceSessionService;

#[derive(Clone)]
pub struct DaemonOperations {
    pub command: Arc<CommandOperationService>,
    // Kept private so gateways cannot treat internal orchestration as tool-call operations.
    #[allow(dead_code)]
    workspace_session: Arc<WorkspaceSessionService>,
    #[allow(dead_code)]
    workspace_remount: Arc<WorkspaceRemountService>,
}

impl DaemonOperations {
    #[must_use]
    pub fn new(
        workspace_session: Arc<WorkspaceSessionService>,
        command: Arc<CommandOperationService>,
        workspace_remount: Arc<WorkspaceRemountService>,
    ) -> Self {
        Self {
            command,
            workspace_session,
            workspace_remount,
        }
    }
}
