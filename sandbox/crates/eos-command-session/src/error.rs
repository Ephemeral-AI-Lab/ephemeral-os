use thiserror::Error;

#[derive(Debug, Error)]
pub enum CommandSessionError {
    #[error("{0}")]
    Workspace(#[from] eos_workspace_api::WorkspaceApiError),
    #[error("command session not found: {0}")]
    NotFound(String),
    #[error("invalid command session request: {0}")]
    InvalidRequest(String),
    #[error("unsupported command session operation: {0}")]
    Unsupported(String),
    #[error("command session io error: {0}")]
    Io(String),
}

impl From<std::io::Error> for CommandSessionError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error.to_string())
    }
}
