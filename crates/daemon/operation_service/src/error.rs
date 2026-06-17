use thiserror::Error;

#[derive(Debug, Error)]
pub enum OperationServiceError {
    #[error(transparent)]
    WorkspaceManager(#[from] crate::workspace::WorkspaceManagerError),
}
