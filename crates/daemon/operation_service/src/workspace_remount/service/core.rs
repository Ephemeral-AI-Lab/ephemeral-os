use std::sync::Arc;

use crate::workspace_remount::{CommandRemountCoordinator, RemountWorkspaceSession};

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
