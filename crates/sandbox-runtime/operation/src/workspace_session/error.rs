use thiserror::Error;

use crate::workspace_crate::{WorkspaceError, WorkspaceSessionId};

#[derive(Debug, Error)]
pub enum WorkspaceSessionError {
    #[error(transparent)]
    Workspace(#[from] WorkspaceError),

    #[error("workspace session manager lock poisoned")]
    LockPoisoned,

    #[error("workspace session already exists: {workspace_session_id:?}")]
    DuplicateWorkspaceSessionId {
        workspace_session_id: WorkspaceSessionId,
    },

    #[error("workspace session not found: {workspace_session_id:?}")]
    NotFound {
        workspace_session_id: WorkspaceSessionId,
    },

    #[error("workspace remount already pending: {workspace_session_id:?}")]
    RemountAlreadyPending {
        workspace_session_id: WorkspaceSessionId,
    },

    #[error("workspace remount blocked after failure: {workspace_session_id:?}")]
    RemountBlocked {
        workspace_session_id: WorkspaceSessionId,
    },

    #[error("workspace remount is not pending: {workspace_session_id:?}")]
    RemountNotPending {
        workspace_session_id: WorkspaceSessionId,
    },

    #[error("workspace remount returned mismatched workspace session id: expected {expected:?}, actual {actual:?}")]
    RemountWorkspaceSessionIdMismatch {
        expected: WorkspaceSessionId,
        actual: WorkspaceSessionId,
    },

    #[error(
        "workspace session publish captured changes failed for {workspace_session_id:?}: {error}"
    )]
    PublishCapturedChanges {
        workspace_session_id: WorkspaceSessionId,
        error: String,
    },

    #[error(
        "workspace cleanup after create failure failed for {workspace_session_id:?}: {rollback_error}"
    )]
    CreateRollbackFailed {
        workspace_session_id: WorkspaceSessionId,
        insert_error: Box<WorkspaceSessionError>,
        rollback_error: WorkspaceError,
    },
}

impl WorkspaceSessionError {
    pub(crate) fn not_found(workspace_session_id: &WorkspaceSessionId) -> Self {
        Self::NotFound {
            workspace_session_id: workspace_session_id.clone(),
        }
    }
}
