use std::sync::Arc;

use crate::command::{CommandRemountInspection, CommandRemountQuiesce};
use crate::workspace_crate::{RemountWorkspaceRequest, WorkspaceId};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionHandler};

pub trait CommandRemountCoordinator: Send + Sync {
    fn begin_workspace_remount_quiesce(
        &self,
        workspace_session_id: &WorkspaceId,
    ) -> CommandRemountQuiesce;
}

pub trait RemountWorkspaceSession: Send + Sync {
    fn begin_remount(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    fn apply_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError>;

    fn apply_and_finish_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let updated = self.apply_remount(handler, request)?;
        self.finish_remount(handler.workspace_session_id.clone())?;
        Ok(updated)
    }

    fn finish_remount(
        &self,
        workspace_session_id: WorkspaceId,
    ) -> Result<(), WorkspaceSessionError>;

    fn finish_or_block_remount(
        &self,
        workspace_session_id: WorkspaceId,
        reason: Option<String>,
    ) -> Result<(), WorkspaceSessionError>;

    fn is_remount_pending(&self, workspace_session_id: &WorkspaceId) -> bool;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceRemountReport {
    pub workspace_session_id: WorkspaceId,
    pub remounted: bool,
    pub blocked_reason: Option<String>,
    pub command_inspection: CommandRemountInspection,
    pub updated_handler: Option<WorkspaceSessionHandler>,
}

pub struct WorkspaceRemountService {
    workspace: Arc<dyn RemountWorkspaceSession>,
    command: Arc<dyn CommandRemountCoordinator>,
}

impl WorkspaceRemountService {
    #[must_use]
    pub fn new(
        workspace: Arc<dyn RemountWorkspaceSession>,
        command: Arc<dyn CommandRemountCoordinator>,
    ) -> Self {
        Self { workspace, command }
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
