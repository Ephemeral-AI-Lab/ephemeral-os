use std::collections::HashMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::isolated_network_setup::VethAllocation;
use crate::lifecycle::remount::WorkspaceRemountState;
use crate::model::NetworkMode;
use crate::overlay::dirs::OverlayDirs;

pub(crate) const HANDLE_PREFIX: &str = "eos-iws-";
pub(crate) const CGROUP_ROOT: &str = "/sys/fs/cgroup";

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct WorkspaceModeId(pub String);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceModeSnapshot {
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub layer_paths: Vec<PathBuf>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DnsConfiguration {
    pub fallback_applied: bool,
    pub previous_first_nameserver: Option<String>,
}

#[derive(Debug, Clone)]
pub struct WorkspaceModeHandle {
    pub workspace_id: WorkspaceModeId,
    pub network: NetworkMode,
    pub caller_id: String,
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_root: String,
    pub dirs: OverlayDirs,
    pub layer_paths: Vec<PathBuf>,
    pub ns_fds: HashMap<String, i32>,
    pub holder_pid: i32,
    pub readiness_fd: i32,
    pub control_fd: i32,
    pub veth: Option<VethAllocation>,
    pub cgroup_path: Option<PathBuf>,
    pub dns_configuration: DnsConfiguration,
    pub remount_state: WorkspaceRemountState,
    pub created_at: f64,
    pub last_activity: f64,
}

#[derive(Debug, Clone)]
pub struct WorkspaceModeContext {
    pub caller_id: String,
    pub workspace_handle_id: String,
    pub network: NetworkMode,
    pub layer_stack_root: PathBuf,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_root: PathBuf,
    pub scratch_dir: PathBuf,
    pub upperdir: PathBuf,
    pub workdir: PathBuf,
    pub layer_paths: Vec<PathBuf>,
    pub ns_fds: HashMap<String, i32>,
    pub cgroup_path: Option<PathBuf>,
}

/// Namespace file descriptors owned by a holder-backed workspace.
#[doc(hidden)]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceNamespaceFds {
    user: Option<i32>,
    mnt: Option<i32>,
    pid: Option<i32>,
    net: Option<i32>,
}

impl WorkspaceNamespaceFds {
    #[doc(hidden)]
    #[must_use]
    pub const fn from_raw_parts(
        user: Option<i32>,
        mnt: Option<i32>,
        pid: Option<i32>,
        net: Option<i32>,
    ) -> Self {
        Self {
            user,
            mnt,
            pid,
            net,
        }
    }

    #[doc(hidden)]
    #[must_use]
    pub const fn user(&self) -> Option<i32> {
        self.user
    }

    #[doc(hidden)]
    #[must_use]
    pub const fn mnt(&self) -> Option<i32> {
        self.mnt
    }

    #[doc(hidden)]
    #[must_use]
    pub const fn pid(&self) -> Option<i32> {
        self.pid
    }

    #[doc(hidden)]
    #[must_use]
    pub const fn net(&self) -> Option<i32> {
        self.net
    }
}

#[must_use]
pub(crate) fn workspace_namespace_fds_from_map(
    map: &HashMap<String, i32>,
) -> Option<WorkspaceNamespaceFds> {
    if map.is_empty() {
        return None;
    }
    let fd = |name: &str| map.get(name).copied();
    Some(WorkspaceNamespaceFds {
        user: fd("user"),
        mnt: fd("mnt"),
        pid: fd("pid"),
        net: fd("net"),
    })
}
