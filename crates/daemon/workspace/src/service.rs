use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};

use crate::error::WorkspaceError;
use crate::model::{
    CallerId, CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest, ReadonlySnapshotHandle,
    RemountWorkspaceRequest, RemountWorkspaceResult, WorkspaceHandle, WorkspaceId,
};
use crate::profile::{
    IsolatedNetworkError, ResourceCaps, WorkspaceModeId, WorkspaceModeManager,
    WorkspaceModeSnapshot,
};

mod impls;

pub struct WorkspaceRuntimeService {
    backend: WorkspaceRuntimeBackend,
}

pub(crate) struct WorkspaceRuntimeState {
    pub(crate) manager: WorkspaceModeManager,
    pub(crate) layer_stack_roots: HashMap<WorkspaceModeId, PathBuf>,
}

enum WorkspaceRuntimeBackend {
    Runtime(Box<Mutex<WorkspaceRuntimeState>>),
    Hooks(WorkspaceRuntimeHooks),
}

type CreateWorkspaceHook =
    dyn Fn(CreateWorkspaceRequest) -> Result<WorkspaceHandle, WorkspaceError> + Send + Sync;
type CaptureChangesHook = dyn Fn(&WorkspaceHandle, CaptureChangesRequest) -> Result<CapturedWorkspaceChanges, WorkspaceError>
    + Send
    + Sync;
type RemountWorkspaceHook = dyn Fn(&WorkspaceHandle, RemountWorkspaceRequest) -> Result<RemountWorkspaceResult, WorkspaceError>
    + Send
    + Sync;
type DestroyWorkspaceHook = dyn Fn(WorkspaceHandle, DestroyWorkspaceRequest) -> Result<DestroyWorkspaceResult, WorkspaceError>
    + Send
    + Sync;
type LatestSnapshotHook =
    dyn Fn(LatestSnapshotRequest) -> Result<ReadonlySnapshotHandle, WorkspaceError> + Send + Sync;

#[doc(hidden)]
pub struct WorkspaceRuntimeHooks {
    pub create_workspace: Box<CreateWorkspaceHook>,
    pub capture_changes: Box<CaptureChangesHook>,
    pub remount_workspace: Box<RemountWorkspaceHook>,
    pub destroy_workspace: Box<DestroyWorkspaceHook>,
    pub latest_snapshot: Box<LatestSnapshotHook>,
}

impl WorkspaceRuntimeService {
    #[must_use]
    pub fn new(manager: WorkspaceModeManager) -> Self {
        Self {
            backend: WorkspaceRuntimeBackend::Runtime(Box::new(Mutex::new(
                WorkspaceRuntimeState {
                    manager,
                    layer_stack_roots: HashMap::new(),
                },
            ))),
        }
    }

    #[must_use]
    pub fn with_scratch_root(caps: ResourceCaps, scratch_root: PathBuf) -> Self {
        Self::new(WorkspaceModeManager::with_scratch_root(caps, scratch_root))
    }

    #[doc(hidden)]
    #[must_use]
    pub fn from_hooks_for_test(hooks: WorkspaceRuntimeHooks) -> Self {
        Self {
            backend: WorkspaceRuntimeBackend::Hooks(hooks),
        }
    }

    pub(crate) const fn hooks(&self) -> Option<&WorkspaceRuntimeHooks> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(_) => None,
            WorkspaceRuntimeBackend::Hooks(hooks) => Some(hooks),
        }
    }

    pub(crate) fn lock_state(
        &self,
    ) -> Result<MutexGuard<'_, WorkspaceRuntimeState>, WorkspaceError> {
        match &self.backend {
            WorkspaceRuntimeBackend::Runtime(state) => {
                state.lock().map_err(|_| WorkspaceError::Setup {
                    step: "workspace runtime state lock poisoned".to_owned(),
                })
            }
            WorkspaceRuntimeBackend::Hooks(_) => Err(WorkspaceError::Setup {
                step: "workspace runtime hooks do not expose concrete state".to_owned(),
            }),
        }
    }
}

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

pub(crate) fn ensure_configured_workspace_root(
    manager: &WorkspaceModeManager,
    requested: &Path,
) -> Result<(), WorkspaceError> {
    let configured = manager
        .validated_workspace_root()
        .map_err(|error| workspace_error_from_mode_error(None, error))?;
    if requested != Path::new(&configured) {
        return Err(WorkspaceError::InvalidRequest {
            field: "workspace_root",
            message: format!(
                "must match configured workspace root {configured}: {}",
                requested.display()
            ),
        });
    }
    Ok(())
}

pub(crate) fn workspace_error_from_mode_error(
    owner: Option<&CallerId>,
    error: IsolatedNetworkError,
) -> WorkspaceError {
    match error {
        IsolatedNetworkError::FeatureDisabled => WorkspaceError::FeatureDisabled,
        IsolatedNetworkError::InvalidArgument(message) => WorkspaceError::InvalidRequest {
            field: "workspace",
            message,
        },
        IsolatedNetworkError::AlreadyOpen { .. } => WorkspaceError::InvalidRequest {
            field: "caller_id",
            message: "caller already has an open workspace".to_owned(),
        },
        IsolatedNetworkError::NotOpen => WorkspaceError::NotOpen {
            owner: owner.cloned().unwrap_or_else(|| CallerId(String::new())),
        },
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
        return Err(WorkspaceError::NotOpen {
            owner: handle.owner.clone(),
        });
    };
    if mode_handle.caller_id != handle.owner.0 {
        return Err(WorkspaceError::InvalidRequest {
            field: "owner",
            message: format!(
                "workspace {} is owned by {}, not {}",
                handle.id.0, mode_handle.caller_id, handle.owner.0
            ),
        });
    }
    Ok(mode_id)
}

pub(crate) fn snapshot_from_public(
    snapshot: &crate::model::LayerStackSnapshotRef,
) -> layerstack::service::Snapshot {
    layerstack::service::Snapshot {
        lease_id: snapshot.lease_id.0.clone(),
        manifest_version: snapshot.manifest_version,
        root_hash: snapshot.root_hash.clone(),
        layer_paths: snapshot.layer_paths.clone(),
    }
}

pub(crate) fn mode_snapshot_from_layerstack(
    snapshot: layerstack::service::Snapshot,
) -> WorkspaceModeSnapshot {
    WorkspaceModeSnapshot {
        lease_id: snapshot.lease_id,
        manifest_version: snapshot.manifest_version,
        manifest_root_hash: snapshot.root_hash,
        layer_paths: snapshot.layer_paths,
    }
}

pub(crate) fn workspace_id_from_mode_id(mode_id: &WorkspaceModeId) -> WorkspaceId {
    WorkspaceId(mode_id.0.clone())
}

#[cfg(test)]
#[path = "../tests/unit/service.rs"]
mod tests;
