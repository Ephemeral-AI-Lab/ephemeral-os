use std::collections::{BTreeMap, HashMap};
use std::fmt;
use std::path::PathBuf;

use layerstack::service::BoundedCaptureOptions;
use layerstack::CaptureRouteStats;

use crate::overlay::tree::TreeResourceStats;
use crate::profile::WorkspaceModeHandle;

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

/// Isolation profile for a private mounted workspace.
///
/// The enum name reflects the current concrete split: whether the workspace
/// preserves host network access or adds a dedicated network boundary. It does
/// not encode lifecycle length, publication behavior, or whether the caller is
/// running a one-shot operation. Those decisions belong to the runtime or
/// operation layer that owns the workspace handle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkMode {
    /// Host-compatible profile: private overlay and holder namespace stack with
    /// host network access.
    Host,
    /// Fully isolated profile: private overlay and holder namespace stack plus
    /// a dedicated network boundary.
    Isolated,
}

#[derive(Clone, PartialEq, Eq)]
pub struct WorkspaceHandle {
    pub id: WorkspaceId,
    pub owner: CallerId,
    pub workspace_root: PathBuf,
    pub network: NetworkMode,
    pub base_revision: BaseRevision,
    pub snapshot: LayerStackSnapshotRef,
    pub launch: Option<WorkspaceLaunchContext>,
}

impl fmt::Debug for WorkspaceHandle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WorkspaceHandle")
            .field("id", &self.id)
            .field("owner", &self.owner)
            .field("workspace_root", &self.workspace_root)
            .field("network", &self.network)
            .field("base_revision", &self.base_revision)
            .field("snapshot", &self.snapshot)
            .field("launch", &self.launch.as_ref().map(|_| "<available>"))
            .finish()
    }
}

#[derive(Clone, PartialEq, Eq)]
pub struct WorkspaceLaunchContext {
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
    pub namespace_fds: Option<WorkspaceLaunchNamespaceFds>,
    pub cgroup_path: Option<PathBuf>,
}

impl fmt::Debug for WorkspaceLaunchContext {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WorkspaceLaunchContext")
            .field("storage", &"<hidden>")
            .field(
                "namespaces",
                &self.namespace_fds.as_ref().map(|_| "<available>"),
            )
            .field("cgroup", &self.cgroup_path.as_ref().map(|_| "<available>"))
            .finish()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct WorkspaceLaunchNamespaceFds {
    pub user: Option<i32>,
    pub mnt: Option<i32>,
    pub pid: Option<i32>,
    pub net: Option<i32>,
}

impl fmt::Debug for WorkspaceLaunchNamespaceFds {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let available = |fd: Option<i32>| fd.map(|_| "<available>");
        f.debug_struct("WorkspaceLaunchNamespaceFds")
            .field("user", &available(self.user))
            .field("mnt", &available(self.mnt))
            .field("pid", &available(self.pid))
            .field("net", &available(self.net))
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateWorkspaceRequest {
    pub caller_id: CallerId,
    pub workspace_root: PathBuf,
    pub layer_stack_root: PathBuf,
    pub network: NetworkMode,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureChangesRequest {
    pub bounds: BoundedCaptureOptions,
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
pub struct CapturedWorkspaceChanges {
    pub workspace_id: WorkspaceId,
    pub base_revision: BaseRevision,
    pub changed_paths: Vec<String>,
    pub changed_path_kinds: BTreeMap<String, ChangedPathKind>,
    pub protected_drops: Vec<ProtectedPathDrop>,
    pub stats: Option<TreeResourceStats>,
    pub changes: Vec<layerstack::LayerChange>,
    pub route_stats: CaptureRouteStats,
    pub metadata_path_count: usize,
    pub spool_dir: Option<PathBuf>,
}

pub type CaptureChangesResult = CapturedWorkspaceChanges;

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

#[derive(Debug, Clone, Default, PartialEq)]
pub struct DestroyWorkspaceRequest {
    pub grace_s: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DestroyWorkspaceResult {
    pub workspace_id: WorkspaceId,
    pub owner: CallerId,
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
            launch: Some(WorkspaceLaunchContext {
                upperdir: handle.dirs.upperdir.clone(),
                workdir: handle.dirs.workdir.clone(),
                namespace_fds: namespace_fds_from_map(&handle.ns_fds),
                cgroup_path: handle.cgroup_path.clone(),
            }),
        }
    }
}

fn namespace_fds_from_map(ns_fds: &HashMap<String, i32>) -> Option<WorkspaceLaunchNamespaceFds> {
    if ns_fds.is_empty() {
        return None;
    }
    let fd = |name: &str| ns_fds.get(name).copied();
    Some(WorkspaceLaunchNamespaceFds {
        user: fd("user"),
        mnt: fd("mnt"),
        pid: fd("pid"),
        net: fd("net"),
    })
}

#[cfg(test)]
#[path = "../tests/unit/model.rs"]
mod tests;
