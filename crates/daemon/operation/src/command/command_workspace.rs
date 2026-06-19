use std::path::{Path, PathBuf};

use layerstack::service::{
    LeaseReleaseHandle, LeaseReleaseReport, Snapshot, SnapshotNormalization,
};
use workspace::overlay::dirs::{allocate_overlay_dirs, DirAllocationError, OverlayDirs};

#[derive(Debug)]
pub struct OneShotCommandWorkspace {
    pub(crate) layer_stack_root: PathBuf,
    pub(crate) workspace_root: PathBuf,
    pub(crate) snapshot: Snapshot,
    pub(crate) normalization: SnapshotNormalization,
    dirs: OverlayDirs,
    pub(crate) lease: LeaseReleaseHandle,
}

impl OneShotCommandWorkspace {
    /// Allocate fresh overlay dirs for a one-shot command workspace.
    ///
    /// # Errors
    ///
    /// Returns [`DirAllocationError`] when scratch directories cannot be created.
    pub fn create_overlay(
        layer_stack_root: PathBuf,
        workspace_root: PathBuf,
        snapshot: Snapshot,
        normalization: SnapshotNormalization,
        scratch_root: &Path,
        kind: &str,
        token: &str,
        lease: LeaseReleaseHandle,
    ) -> Result<Self, DirAllocationError> {
        Ok(Self {
            layer_stack_root,
            workspace_root,
            snapshot,
            normalization,
            dirs: allocate_overlay_dirs(scratch_root, kind, token)?,
            lease,
        })
    }

    #[cfg(test)]
    #[must_use]
    pub(crate) const fn new_for_test(
        layer_stack_root: PathBuf,
        workspace_root: PathBuf,
        snapshot: Snapshot,
        normalization: SnapshotNormalization,
        dirs: OverlayDirs,
        lease: LeaseReleaseHandle,
    ) -> Self {
        Self {
            layer_stack_root,
            workspace_root,
            snapshot,
            normalization,
            dirs,
            lease,
        }
    }

    #[must_use]
    pub(crate) const fn dirs(&self) -> &OverlayDirs {
        &self.dirs
    }

    #[must_use]
    pub(crate) fn layer_stack_root(&self) -> &Path {
        &self.layer_stack_root
    }

    #[must_use]
    pub(crate) fn workspace_root(&self) -> &Path {
        &self.workspace_root
    }

    #[must_use]
    pub(crate) const fn snapshot(&self) -> &Snapshot {
        &self.snapshot
    }

    #[must_use]
    pub(crate) const fn normalization(&self) -> &SnapshotNormalization {
        &self.normalization
    }

    #[must_use]
    pub(crate) fn release_lease(&self) -> LeaseReleaseReport {
        self.lease.release()
    }
}

impl Drop for OneShotCommandWorkspace {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dirs.run_dir);
    }
}
