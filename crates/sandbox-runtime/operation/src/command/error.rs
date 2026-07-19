use thiserror::Error;

use sandbox_runtime_namespace_execution::NamespaceExecutionId;

#[derive(Debug, Error)]
pub enum CommandServiceError {
    #[error(transparent)]
    WorkspaceSession(#[from] crate::workspace_session::WorkspaceSessionError),

    #[error(transparent)]
    LayerStack(Box<crate::layerstack::LayerStackServiceError>),

    #[error("invalid command request: {message}")]
    InvalidCommand { message: String },

    #[error("command not found: {command_session_id:?}")]
    CommandNotFound {
        command_session_id: NamespaceExecutionId,
    },

    #[error("command already completed: {command_session_id:?}")]
    CommandAlreadyCompleted {
        command_session_id: NamespaceExecutionId,
    },

    #[error("command io failed for {command_session_id:?}: {error}")]
    CommandIo {
        command_session_id: NamespaceExecutionId,
        error: String,
    },

    #[error("command admission refused: {max_active_commands} active commands in flight")]
    CommandAdmissionOverloaded { max_active_commands: usize },

    #[error("command already exists: {command_session_id:?}")]
    CommandAlreadyExists {
        command_session_id: NamespaceExecutionId,
    },

    #[error("command finalization failed for {command_session_id:?}: {error}")]
    CommandFinalizationFailed {
        command_session_id: NamespaceExecutionId,
        error: String,
    },
}

impl From<crate::layerstack::LayerStackServiceError> for CommandServiceError {
    fn from(error: crate::layerstack::LayerStackServiceError) -> Self {
        Self::LayerStack(Box::new(error))
    }
}
