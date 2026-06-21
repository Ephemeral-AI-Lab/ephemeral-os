use thiserror::Error;

use crate::command::CommandSessionId;
use crate::workspace_crate::WorkspaceSessionId;

#[derive(Debug, Error)]
pub enum CgroupMonitorServiceError {
    #[error(transparent)]
    WorkspaceSession(#[from] crate::workspace_session::WorkspaceSessionError),

    #[error("invalid cgroup monitor request: {message}")]
    InvalidInput { message: String },

    #[error("cgroup monitor target not found for workspace session {workspace_session_id:?}")]
    SessionTargetNotFound {
        workspace_session_id: WorkspaceSessionId,
    },

    #[error(
        "cgroup monitor target not found for workspace session {workspace_session_id:?} and command session {command_session_id:?}"
    )]
    CommandTargetNotFound {
        workspace_session_id: WorkspaceSessionId,
        command_session_id: CommandSessionId,
    },
}
