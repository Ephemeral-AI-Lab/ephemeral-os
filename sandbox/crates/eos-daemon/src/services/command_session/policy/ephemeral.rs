use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, PoisonError};

use eos_ephemeral_workspace::command_session::types::{
    EphemeralCommandFinalizeContext, EphemeralCommandPrepareContext, EphemeralCommandSessionPort,
};
use eos_ephemeral_workspace::{
    CallerId, EphemeralRunDirs, EphemeralSnapshot, EphemeralWorkspace, EphemeralWorkspaceError,
    EphemeralWorkspaceOps, InvocationId, PathChange, PublishOutcome, WorkspacePublisherPort,
    WorkspaceRoot as EphemeralWorkspaceRoot,
};
use eos_layerstack::LayerStack;
use eos_protocol::LayerChange;
use eos_workspace_api::{
    CommandWorkspacePolicy, FinalizeCommandRequest, PrepareCommandRequest,
    PreparedCommandWorkspace, WorkspaceApiError, WorkspaceCommandOutcome,
};

use crate::response_timings::{resource_timings, timing_map};
use crate::services::overlay::{ephemeral_dir_allocator, DaemonPublisherPort};

pub(crate) struct EphemeralCommandPolicy {
    root: PathBuf,
    workspace_root: PathBuf,
    scratch_root: PathBuf,
    state: Mutex<Option<EphemeralCommandPolicyState>>,
}

pub(crate) struct EphemeralCommandWorkspace {
    pub(crate) root: PathBuf,
    pub(crate) lease_id: String,
    pub(crate) manifest_version: i64,
    pub(crate) manifest_root_hash: String,
    pub(crate) layer_paths: Vec<PathBuf>,
    pub(crate) workspace_root: PathBuf,
    pub(crate) dirs: EphemeralRunDirs,
}

struct EphemeralCommandPolicyState {
    caller_id: String,
    command_session_id: String,
    workspace: EphemeralCommandWorkspace,
}

pub(crate) struct EphemeralCommandPreparePort<'a> {
    root: &'a Path,
    workspace_root: &'a Path,
    session_dir: PathBuf,
    final_path: PathBuf,
}

impl EphemeralCommandPolicy {
    pub(crate) fn new(root: PathBuf, workspace_root: PathBuf, scratch_root: PathBuf) -> Self {
        Self {
            root,
            workspace_root,
            scratch_root,
            state: Mutex::new(None),
        }
    }
}

impl CommandWorkspacePolicy for EphemeralCommandPolicy {
    fn prepare_command_workspace(
        &self,
        request: PrepareCommandRequest,
    ) -> Result<PreparedCommandWorkspace, WorkspaceApiError> {
        let session_dir = self.scratch_root.join(&request.command_session_id);
        std::fs::create_dir_all(&session_dir).map_err(workspace_api_error)?;
        let final_path = session_dir.join("final.json");
        std::fs::write(
            session_dir.join("metadata.json"),
            serde_json::to_vec_pretty(&serde_json::json!({
                "command_session_id": request.command_session_id,
                "caller_id": request.caller_id,
                "invocation_id": request.invocation_id,
                "command": request.cmd,
                "status": "running",
            }))
            .map_err(workspace_api_error)?,
        )
        .map_err(workspace_api_error)?;

        let caller_id = request.caller_id.clone();
        let command_session_id = request.command_session_id.clone();
        let prepared = EphemeralWorkspaceOps::new(EphemeralCommandPreparePort::new(
            &self.root,
            &self.workspace_root,
            session_dir,
            final_path,
        ))
        .prepare_command_workspace(request)?;
        let snapshot: EphemeralSnapshot = serde_json::from_value(
            prepared
                .finalize_context
                .get("snapshot")
                .cloned()
                .ok_or_else(|| {
                    WorkspaceApiError::new(
                        "daemon_command_workspace_error",
                        "missing command snapshot",
                    )
                })?,
        )
        .map_err(workspace_api_error)?;
        let dirs: EphemeralRunDirs =
            serde_json::from_value(prepared.finalize_context.get("dirs").cloned().ok_or_else(
                || WorkspaceApiError::new("daemon_command_workspace_error", "missing command dirs"),
            )?)
            .map_err(workspace_api_error)?;
        let state = EphemeralCommandPolicyState {
            caller_id,
            command_session_id,
            workspace: EphemeralCommandWorkspace {
                root: self.root.clone(),
                lease_id: snapshot.lease_id,
                manifest_version: snapshot.manifest_version,
                manifest_root_hash: snapshot.manifest_root_hash,
                layer_paths: snapshot.layer_paths,
                workspace_root: self.workspace_root.clone(),
                dirs,
            },
        };
        *lock(&self.state) = Some(state);
        Ok(prepared)
    }

    fn finalize_command_workspace(
        &self,
        request: FinalizeCommandRequest,
    ) -> Result<WorkspaceCommandOutcome, WorkspaceApiError> {
        let state = lock(&self.state).take().ok_or_else(|| {
            WorkspaceApiError::new(
                "daemon_command_workspace_error",
                "ephemeral command workspace is not prepared",
            )
        })?;
        EphemeralWorkspaceOps::new(EphemeralCommandFinalizePort {
            caller_id: &state.caller_id,
            invocation_id: &state.command_session_id,
            workspace: &state.workspace,
        })
        .finalize_command_workspace(request)
    }
}

impl<'a> EphemeralCommandPreparePort<'a> {
    pub(crate) fn new(
        root: &'a Path,
        workspace_root: &'a Path,
        session_dir: PathBuf,
        final_path: PathBuf,
    ) -> Self {
        Self {
            root,
            workspace_root,
            session_dir,
            final_path,
        }
    }
}

impl EphemeralCommandSessionPort for EphemeralCommandPreparePort<'_> {
    fn prepare_context(&self) -> Result<EphemeralCommandPrepareContext, WorkspaceApiError> {
        Ok(EphemeralCommandPrepareContext {
            layer_stack_root: self.root.to_path_buf(),
            workspace_root: self.workspace_root.to_path_buf(),
            writable_root: ephemeral_dir_allocator()
                .map_err(workspace_api_error)?
                .writable_root,
            session_dir: self.session_dir.clone(),
            final_path: self.final_path.clone(),
        })
    }

    fn acquire_snapshot(
        &self,
        request_id: &str,
    ) -> Result<EphemeralSnapshot, EphemeralWorkspaceError> {
        let lease = LayerStack::open(self.root.to_path_buf())
            .and_then(|mut stack| stack.acquire_snapshot(request_id))
            .map_err(|error| EphemeralWorkspaceError::SnapshotAcquire {
                reason: error.to_string(),
            })?;
        Ok(EphemeralSnapshot {
            lease_id: lease.lease_id,
            manifest_version: lease.manifest_version,
            manifest_root_hash: lease.root_hash,
            layer_paths: lease.layer_paths.into_iter().map(PathBuf::from).collect(),
        })
    }

    fn release_snapshot(&self, lease_id: &str) -> Result<(), EphemeralWorkspaceError> {
        LayerStack::open(self.root.to_path_buf())
            .and_then(|mut stack| stack.release_lease(lease_id))
            .map(|_| ())
            .map_err(|error| EphemeralWorkspaceError::LeaseRelease {
                lease_id: lease_id.to_owned(),
                reason: error.to_string(),
            })
    }
}

pub(crate) struct EphemeralCommandFinalizePort<'a> {
    pub(crate) caller_id: &'a str,
    pub(crate) invocation_id: &'a str,
    pub(crate) workspace: &'a EphemeralCommandWorkspace,
}

impl EphemeralCommandSessionPort for EphemeralCommandFinalizePort<'_> {
    fn finalize_context(&self) -> Result<EphemeralCommandFinalizeContext, WorkspaceApiError> {
        let manifest = LayerStack::open(self.workspace.root.clone())
            .and_then(|stack| stack.read_active_manifest())
            .map_err(workspace_api_error)?;
        Ok(EphemeralCommandFinalizeContext {
            workspace: EphemeralWorkspace {
                layer_stack_root: EphemeralWorkspaceRoot(self.workspace.root.clone()),
                workspace_root: self.workspace.workspace_root.clone(),
                caller_id: CallerId(self.caller_id.to_owned()),
                invocation_id: InvocationId(self.invocation_id.to_owned()),
                snapshot: EphemeralSnapshot {
                    lease_id: self.workspace.lease_id.clone(),
                    manifest_version: self.workspace.manifest_version,
                    manifest_root_hash: self.workspace.manifest_root_hash.clone(),
                    layer_paths: self.workspace.layer_paths.clone(),
                },
                dirs: self.workspace.dirs.clone(),
            },
            base_timings: timing_map(resource_timings(&manifest, 0)),
        })
    }

    fn publish_upperdir_changes(
        &self,
        root: &EphemeralWorkspaceRoot,
        snapshot: &EphemeralSnapshot,
        changes: &[LayerChange],
        path_kinds: &[PathChange],
    ) -> Result<PublishOutcome, EphemeralWorkspaceError> {
        DaemonPublisherPort::new(&self.workspace.root)
            .publish_upperdir_changes(root, snapshot, changes, path_kinds)
    }
}

fn workspace_api_error(error: impl std::fmt::Display) -> WorkspaceApiError {
    WorkspaceApiError::new("daemon_command_workspace_error", error.to_string())
}

impl Drop for EphemeralCommandPolicyState {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.workspace.dirs.run_dir);
        let _ = LayerStack::open(self.workspace.root.clone())
            .and_then(|mut stack| stack.release_lease(&self.workspace.lease_id));
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}
