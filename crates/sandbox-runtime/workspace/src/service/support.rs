use std::path::Path;

use crate::error::WorkspaceError;
use crate::model::{WorkspaceHandle, WorkspaceSessionId};
use crate::profile::WorkspaceProfileError;
use crate::service::WorkspaceRuntimeState;

pub(crate) fn ensure_absolute(path: &Path, field: &'static str) -> Result<(), WorkspaceError> {
    if !path.is_absolute() {
        return Err(WorkspaceError::InvalidRequest {
            field,
            message: format!("must be absolute: {}", path.display()),
        });
    }
    Ok(())
}

pub(crate) fn workspace_error_from_profile_error(error: WorkspaceProfileError) -> WorkspaceError {
    match error {
        WorkspaceProfileError::InvalidArgument(message) => WorkspaceError::InvalidRequest {
            field: "workspace",
            message,
        },
        WorkspaceProfileError::NotOpen => WorkspaceError::NotOpen,
        WorkspaceProfileError::SetupFailed { step } => WorkspaceError::Setup { step },
        WorkspaceProfileError::NetworkUnavailable(message) => WorkspaceError::Network { message },
    }
}

pub(crate) fn active_profile_id(
    state: &WorkspaceRuntimeState,
    handle: &WorkspaceHandle,
) -> Result<WorkspaceSessionId, WorkspaceError> {
    let profile_id = handle.id.clone();
    if !state.manager.handles.contains_key(&profile_id) {
        return Err(WorkspaceError::NotOpen);
    }
    Ok(profile_id)
}
