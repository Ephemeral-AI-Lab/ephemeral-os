use crate::isolated_network_setup::VethAllocation;
use crate::model::{LayerStackSnapshotRef, NetworkProfile, WorkspaceSessionId};
use crate::overlay::dirs::OverlayDirs;

#[derive(Debug, Clone)]
pub struct MountedWorkspace {
    pub workspace_id: WorkspaceSessionId,
    pub network: NetworkProfile,
    pub snapshot: LayerStackSnapshotRef,
    pub workspace_root: String,
    pub dirs: OverlayDirs,
    pub ns_fds: HolderNsFds,
    pub holder_pid: i32,
    pub readiness_fd: i32,
    pub control_fd: i32,
    pub veth: Option<VethAllocation>,
    pub created_at: f64,
    pub last_activity: f64,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct HolderNsFds {
    pub user: Option<i32>,
    pub mnt: Option<i32>,
    pub pid: Option<i32>,
    pub net: Option<i32>,
}

impl HolderNsFds {
    pub(crate) fn len(self) -> usize {
        self.values().count()
    }

    pub(crate) fn is_empty(self) -> bool {
        self.user.is_none() && self.mnt.is_none() && self.pid.is_none() && self.net.is_none()
    }

    pub(crate) fn values(self) -> impl Iterator<Item = i32> {
        [self.user, self.mnt, self.pid, self.net]
            .into_iter()
            .flatten()
    }
}
