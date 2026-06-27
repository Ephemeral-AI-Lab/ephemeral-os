use std::path::Path;

use crate::error::WorkspaceError;
use crate::session::WorkspaceManagerError;

pub(crate) fn ensure_absolute(path: &Path, field: &'static str) -> Result<(), WorkspaceError> {
    if !path.is_absolute() {
        return Err(WorkspaceError::InvalidRequest {
            field,
            message: format!("must be absolute: {}", path.display()),
        });
    }
    Ok(())
}

pub(crate) fn workspace_error_from_manager_error(error: WorkspaceManagerError) -> WorkspaceError {
    match error {
        WorkspaceManagerError::InvalidArgument(message) => WorkspaceError::InvalidRequest {
            field: "workspace",
            message,
        },
        WorkspaceManagerError::NotOpen => WorkspaceError::NotOpen,
        WorkspaceManagerError::SetupFailed { step } => WorkspaceError::Setup { step },
        WorkspaceManagerError::NetworkUnavailable(message) => WorkspaceError::Network { message },
    }
}
