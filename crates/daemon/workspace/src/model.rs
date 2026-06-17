use std::collections::BTreeMap;
use std::path::PathBuf;

use crate::network_mode::isolated_network::WorkspaceModeHandle;
use crate::overlay::tree::TreeResourceStats;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct WorkspaceId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct CallerId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct LeaseId(pub String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BaseRevision {
    pub version: i64,
    pub root_hash: String,
    pub layer_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerStackSnapshotRef {
    pub lease_id: LeaseId,
    pub manifest_version: i64,
    pub root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}

impl LayerStackSnapshotRef {
    #[must_use]
    pub fn base_revision(&self) -> BaseRevision {
        BaseRevision {
            version: self.manifest_version,
            root_hash: self.root_hash.clone(),
            layer_count: self.layer_paths.len(),
        }
    }
}

impl From<layerstack::service::Snapshot> for LayerStackSnapshotRef {
    fn from(snapshot: layerstack::service::Snapshot) -> Self {
        Self {
            lease_id: LeaseId(snapshot.lease_id),
            manifest_version: snapshot.manifest_version,
            root_hash: snapshot.root_hash,
            layer_paths: snapshot.layer_paths,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkMode {
    Host,
    IsolatedNetwork,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceHandle {
    pub id: WorkspaceId,
    pub owner: CallerId,
    pub workspace_root: PathBuf,
    pub network: NetworkMode,
    pub base_revision: BaseRevision,
    pub snapshot: LayerStackSnapshotRef,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateWorkspaceRequest {
    pub owner: CallerId,
    pub workspace_root: PathBuf,
    pub network: NetworkMode,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RunCommandRequest {
    pub invocation_id: String,
    pub cmd: String,
    pub cwd: Option<PathBuf>,
    pub timeout_seconds: Option<f64>,
    pub yield_time_ms: u64,
    pub remountable: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommandStatus {
    Running,
    Ok,
    Cancelled,
    Error,
    TimedOut,
}

impl CommandStatus {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Ok => "ok",
            Self::Cancelled => "cancelled",
            Self::Error => "error",
            Self::TimedOut => "timed_out",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct RunCommandResult {
    pub status: CommandStatus,
    pub command_id: Option<String>,
    pub exit_code: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    pub changed_paths: Vec<String>,
    pub base_revision: BaseRevision,
    pub published: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureChangesRequest {
    pub materialize_payloads: bool,
    pub include_stats: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChangedPathKind {
    Write,
    Delete,
    Symlink,
    OpaqueDir,
}

impl From<&layerstack::LayerChange> for ChangedPathKind {
    fn from(change: &layerstack::LayerChange) -> Self {
        match change {
            layerstack::LayerChange::Write { .. } | layerstack::LayerChange::WriteFile { .. } => {
                Self::Write
            }
            layerstack::LayerChange::Delete { .. } => Self::Delete,
            layerstack::LayerChange::Symlink { .. } => Self::Symlink,
            layerstack::LayerChange::OpaqueDir { .. } => Self::OpaqueDir,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProtectedPathDropReason {
    UnsupportedSpecialFile,
    InvalidLayerPath,
}

impl From<layerstack::ProtectedPathDropReason> for ProtectedPathDropReason {
    fn from(reason: layerstack::ProtectedPathDropReason) -> Self {
        match reason {
            layerstack::ProtectedPathDropReason::UnsupportedSpecialFile => {
                Self::UnsupportedSpecialFile
            }
            layerstack::ProtectedPathDropReason::InvalidLayerPath => Self::InvalidLayerPath,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtectedPathDrop {
    pub path: String,
    pub reason: ProtectedPathDropReason,
}

impl From<&layerstack::ProtectedPathDrop> for ProtectedPathDrop {
    fn from(drop: &layerstack::ProtectedPathDrop) -> Self {
        Self {
            path: drop.path.as_str().to_owned(),
            reason: ProtectedPathDropReason::from(drop.reason),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureChangesResult {
    pub workspace_id: WorkspaceId,
    pub base_revision: BaseRevision,
    pub changed_paths: Vec<String>,
    pub changed_path_kinds: BTreeMap<String, ChangedPathKind>,
    pub protected_drops: Vec<ProtectedPathDrop>,
    pub stats: Option<TreeResourceStats>,
}

pub type CapturedWorkspaceChanges = CaptureChangesResult;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RemountWorkspaceRequest {
    pub layer_paths: Vec<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RemountWorkspaceResult {
    pub handle: WorkspaceHandle,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LatestSnapshotRequest {
    pub workspace_root: PathBuf,
    pub owner_request_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadonlySnapshotHandle {
    pub view_root: PathBuf,
    pub generation_key: String,
    pub snapshot: LayerStackSnapshotRef,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DestroyWorkspaceRequest {
    pub grace_s: Option<f64>,
    pub cancel_commands: bool,
}

impl Default for DestroyWorkspaceRequest {
    fn default() -> Self {
        Self {
            grace_s: None,
            cancel_commands: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct DestroyWorkspaceResult {
    pub workspace_id: WorkspaceId,
    pub owner: CallerId,
    pub cancelled_commands: usize,
    pub evicted_upperdir_bytes: u64,
    pub lifetime_s: f64,
    pub lease_released: Option<bool>,
    pub lease_release_error: Option<String>,
    pub active_leases_after: usize,
}

impl From<&WorkspaceModeHandle> for WorkspaceHandle {
    fn from(handle: &WorkspaceModeHandle) -> Self {
        Self {
            id: WorkspaceId(handle.workspace_id.0.clone()),
            owner: CallerId(handle.caller_id.clone()),
            workspace_root: PathBuf::from(&handle.workspace_root),
            network: handle.network,
            base_revision: BaseRevision {
                version: handle.manifest_version,
                root_hash: handle.manifest_root_hash.clone(),
                layer_count: handle.layer_paths.len(),
            },
            snapshot: LayerStackSnapshotRef {
                lease_id: LeaseId(handle.lease_id.clone()),
                manifest_version: handle.manifest_version,
                root_hash: handle.manifest_root_hash.clone(),
                layer_paths: handle.layer_paths.clone(),
            },
        }
    }
}

#[cfg(test)]
#[path = "../tests/unit/model.rs"]
mod tests;
