use std::path::Path;

use crate::error::WorkspaceError;
use crate::model::WorkspaceHandle;
use crate::profile::{WorkspaceProfileError, WorkspaceProfileId, WorkspaceProfileSnapshot};
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
) -> Result<WorkspaceProfileId, WorkspaceError> {
    let profile_id = WorkspaceProfileId(handle.id.0.clone());
    let Some(profile_handle) = state.manager.handles.get(&profile_id) else {
        return Err(WorkspaceError::NotOpen);
    };
    let _ = profile_handle;
    Ok(profile_id)
}

pub(crate) fn profile_snapshot_from_layerstack(
    snapshot: sandbox_runtime_layerstack::service::LeasedSnapshot,
) -> WorkspaceProfileSnapshot {
    WorkspaceProfileSnapshot {
        lease_id: snapshot.lease_id,
        manifest_version: snapshot.manifest_version,
        manifest_root_hash: snapshot.root_hash,
        base_manifest: snapshot.manifest,
        layer_paths: snapshot.layer_paths,
    }
}
