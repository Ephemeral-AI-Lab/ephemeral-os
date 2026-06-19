use std::path::{Path, PathBuf};

use layerstack::service::{LeaseReleaseHandle, Snapshot, SnapshotNormalization};
use workspace::overlay::dirs::{allocate_overlay_dirs, DirAllocationError, OverlayDirs};
use workspace::WorkspaceLaunchNamespaceFds;

#[derive(Debug)]
pub struct CommandWorkspace {
    dirs: OverlayDirs,
}

impl CommandWorkspace {
    /// Allocate fresh overlay dirs for a command workspace.
    ///
    /// # Errors
    ///
    /// Returns [`DirAllocationError`] when scratch directories cannot be
    /// created.
    pub fn create_overlay(
        scratch_root: &Path,
        kind: &str,
        token: &str,
    ) -> Result<Self, DirAllocationError> {
        Ok(Self {
            dirs: allocate_overlay_dirs(scratch_root, kind, token)?,
        })
    }

    #[must_use]
    pub const fn from_dirs(dirs: OverlayDirs) -> Self {
        Self { dirs }
    }

    #[must_use]
    pub const fn dirs(&self) -> &OverlayDirs {
        &self.dirs
    }
}

impl Drop for CommandWorkspace {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dirs.run_dir);
    }
}

#[derive(Debug)]
pub struct HostCommandWorkspace {
    pub(crate) layer_stack_root: PathBuf,
    pub(crate) workspace_root: PathBuf,
    pub(crate) snapshot: Snapshot,
    pub(crate) normalization: SnapshotNormalization,
    pub(crate) workspace: CommandWorkspace,
    pub(crate) ns_fds: Option<WorkspaceLaunchNamespaceFds>,
    pub(crate) cgroup_path: Option<PathBuf>,
    pub(crate) lease: LeaseReleaseHandle,
}

impl HostCommandWorkspace {
    #[must_use]
    pub fn new(
        layer_stack_root: PathBuf,
        workspace_root: PathBuf,
        snapshot: Snapshot,
        normalization: SnapshotNormalization,
        workspace: CommandWorkspace,
        ns_fds: Option<WorkspaceLaunchNamespaceFds>,
        cgroup_path: Option<PathBuf>,
        lease: LeaseReleaseHandle,
    ) -> Self {
        Self::from_parts(
            layer_stack_root,
            workspace_root,
            snapshot,
            normalization,
            workspace,
            ns_fds,
            cgroup_path,
            lease,
        )
    }

    #[cfg(test)]
    #[must_use]
    pub(crate) fn new_for_test(
        layer_stack_root: PathBuf,
        workspace_root: PathBuf,
        snapshot: Snapshot,
        normalization: SnapshotNormalization,
        workspace: CommandWorkspace,
        ns_fds: Option<WorkspaceLaunchNamespaceFds>,
        cgroup_path: Option<PathBuf>,
        lease: LeaseReleaseHandle,
    ) -> Self {
        Self::from_parts(
            layer_stack_root,
            workspace_root,
            snapshot,
            normalization,
            workspace,
            ns_fds,
            cgroup_path,
            lease,
        )
    }

    fn from_parts(
        layer_stack_root: PathBuf,
        workspace_root: PathBuf,
        snapshot: Snapshot,
        normalization: SnapshotNormalization,
        workspace: CommandWorkspace,
        ns_fds: Option<WorkspaceLaunchNamespaceFds>,
        cgroup_path: Option<PathBuf>,
        lease: LeaseReleaseHandle,
    ) -> Self {
        Self {
            layer_stack_root,
            workspace_root,
            snapshot,
            normalization,
            workspace,
            ns_fds,
            cgroup_path,
            lease,
        }
    }
}
