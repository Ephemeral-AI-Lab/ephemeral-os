use std::sync::Arc;

use crate::workspace_crate::WorkspaceId;
use crate::workspace_remount::{
    CommandRemountCoordinator, CommandRemountInspection, RemountWorkspaceSession,
};
use crate::workspace_session::WorkspaceSessionHandler;

mod impls;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkspaceRemountOptions {
    pub live_quiesce_timeout_ms: u64,
}

impl Default for WorkspaceRemountOptions {
    fn default() -> Self {
        Self {
            live_quiesce_timeout_ms: 30_000,
        }
    }
}

pub struct WorkspaceRemountService {
    workspace: Arc<dyn RemountWorkspaceSession>,
    command: Arc<dyn CommandRemountCoordinator>,
    options: WorkspaceRemountOptions,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceRemountReport {
    pub workspace_session_id: WorkspaceId,
    pub remounted: bool,
    pub blocked_reason: Option<String>,
    pub command_inspection: CommandRemountInspection,
    pub updated_handler: Option<WorkspaceSessionHandler>,
}

impl WorkspaceRemountService {
    #[must_use]
    pub fn new(
        workspace: Arc<dyn RemountWorkspaceSession>,
        command: Arc<dyn CommandRemountCoordinator>,
        options: WorkspaceRemountOptions,
    ) -> Self {
        Self {
            workspace,
            command,
            options,
        }
    }

    #[must_use]
    pub const fn options(&self) -> WorkspaceRemountOptions {
        self.options
    }

    #[must_use]
    pub fn workspace(&self) -> &Arc<dyn RemountWorkspaceSession> {
        &self.workspace
    }

    #[must_use]
    pub fn command(&self) -> &Arc<dyn CommandRemountCoordinator> {
        &self.command
    }
}
