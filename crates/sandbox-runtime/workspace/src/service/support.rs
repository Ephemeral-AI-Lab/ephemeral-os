use std::path::Path;

use crate::error::WorkspaceError;
use crate::model::WorkspaceHandle;
use crate::profile::{IsolatedNetworkError, WorkspaceModeId, WorkspaceModeSnapshot};
use crate::service::WorkspaceRuntimeState;

pub(crate) fn ensure_non_empty(value: &str, field: &'static str) -> Result<(), WorkspaceError> {
    if value.trim().is_empty() {
        return Err(WorkspaceError::InvalidRequest {
            field,
            message: "must not be empty".to_owned(),
        });
    }
    Ok(())
}

pub(crate) fn ensure_absolute(path: &Path, field: &'static str) -> Result<(), WorkspaceError> {
    if !path.is_absolute() {
        return Err(WorkspaceError::InvalidRequest {
            field,
            message: format!("must be absolute: {}", path.display()),
        });
    }
    Ok(())
}

pub(crate) fn workspace_error_from_mode_error(error: IsolatedNetworkError) -> WorkspaceError {
    match error {
        IsolatedNetworkError::InvalidArgument(message) => WorkspaceError::InvalidRequest {
            field: "workspace",
            message,
        },
        IsolatedNetworkError::AlreadyOpen { .. } => WorkspaceError::InvalidRequest {
            field: "workspace",
            message: "workspace already open".to_owned(),
        },
        IsolatedNetworkError::NotOpen => WorkspaceError::NotOpen,
        IsolatedNetworkError::QuotaExceeded { total_cap } => {
            WorkspaceError::QuotaExceeded { total_cap }
        }
        IsolatedNetworkError::HostRamPressure {
            required_bytes,
            budget_bytes,
        } => WorkspaceError::ResourcePressure {
            required_bytes,
            budget_bytes,
        },
        IsolatedNetworkError::SetupFailed { step } => WorkspaceError::Setup { step },
        IsolatedNetworkError::NetworkUnavailable(message) => WorkspaceError::Network { message },
    }
}

pub(crate) fn active_mode_id(
    state: &WorkspaceRuntimeState,
    handle: &WorkspaceHandle,
) -> Result<WorkspaceModeId, WorkspaceError> {
    let mode_id = WorkspaceModeId(handle.id.0.clone());
    let Some(mode_handle) = state.manager.handles.get(&mode_id) else {
        return Err(WorkspaceError::NotOpen);
    };
    let _ = mode_handle;
    Ok(mode_id)
}

pub(crate) fn mode_snapshot_from_layerstack(
    snapshot: sandbox_runtime_layerstack::service::LeasedSnapshot,
) -> WorkspaceModeSnapshot {
    WorkspaceModeSnapshot {
        lease_id: snapshot.lease_id,
        manifest_version: snapshot.manifest_version,
        manifest_root_hash: snapshot.root_hash,
        layer_paths: snapshot.layer_paths,
    }
}
