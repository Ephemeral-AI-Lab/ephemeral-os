use std::path::{Path, PathBuf};

use crate::model::NetworkMode;
use crate::namespace::NamespaceRuntime;
use crate::overlay::dirs::{allocate_overlay_dirs, OverlayDirs};
use crate::profile::common::{
    new_workspace_handle, teardown_workspace, wire_workspace, WorkspaceHandleSpec,
};
use crate::profile::host_compatible::HostCompatibleProfile;
use crate::profile::manager::IsolatedNetworkError;
use crate::profile::{workspace_namespace_fds_from_map, WorkspaceModeHandle, WorkspaceModeId};

pub use crate::profile::WorkspaceNamespaceFds;

#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum HostWorkspaceError {
    /// Fresh writable directory allocation failed.
    #[error("dir allocation failed at {}: {reason}", path.display())]
    DirAllocation { path: PathBuf, reason: String },
    /// Holder namespace startup or setup failed.
    #[error("namespace setup failed: {step}")]
    NamespaceSetup { step: String },
}

impl From<crate::overlay::dirs::DirAllocationError> for HostWorkspaceError {
    fn from(error: crate::overlay::dirs::DirAllocationError) -> Self {
        Self::DirAllocation {
            path: error.path,
            reason: error.reason,
        }
    }
}

impl HostWorkspaceError {
    pub(crate) fn namespace_setup(error: IsolatedNetworkError) -> Self {
        Self::NamespaceSetup {
            step: error.to_string(),
        }
    }
}

/// Host-compatible overlay-backed private workspace.
///
/// A host workspace owns fresh overlay directories and, when holder-backed,
/// user/mount/PID namespaces while preserving host network access. It
/// deliberately skips the dedicated network boundary, veth, DNS rewrite, and
/// network policy resources used by isolated mode.
///
/// Dropping the workspace removes its run directory (best-effort), so the caller
/// can capture the upperdir on success or just drop on cancel/discard.
#[derive(Debug)]
pub struct HostWorkspace {
    dirs: OverlayDirs,
    holder: Option<HostHolder>,
}

/// Inputs for creating a holder-backed host-compatible workspace.
#[derive(Debug, Clone, Copy)]
pub struct HostNamespaceWorkspaceRequest<'a> {
    pub kind: &'a str,
    pub token: &'a str,
    pub caller_id: &'a str,
    pub workspace_root: &'a Path,
    pub layer_paths: &'a [PathBuf],
    pub setup_timeout_s: f64,
    pub exit_grace_s: f64,
}

#[derive(Debug)]
struct HostHolder {
    handle: WorkspaceModeHandle,
    exit_grace_s: f64,
}

impl HostWorkspace {
    /// Allocate fresh overlay dirs under `scratch_root`.
    ///
    /// `kind` and `token` only shape the scratch directory name (sanitized).
    ///
    /// # Errors
    ///
    /// Returns [`HostWorkspaceError::DirAllocation`] when scratch
    /// directories cannot be created.
    pub fn create(
        scratch_root: &Path,
        kind: &str,
        token: &str,
    ) -> Result<Self, HostWorkspaceError> {
        let dirs = allocate_overlay_dirs(scratch_root, kind, token)?;
        Ok(Self { dirs, holder: None })
    }

    /// Allocate fresh overlay dirs under the daemon runtime writable root.
    ///
    /// This preserves the legacy host-command scratch placement while allowing
    /// higher-level runtime code to own the workspace lifecycle.
    ///
    /// # Errors
    ///
    /// Returns [`HostWorkspaceError::DirAllocation`] when scratch
    /// directories cannot be created.
    pub fn create_runtime_overlay(kind: &str, token: &str) -> Result<Self, HostWorkspaceError> {
        let dirs = crate::overlay::dirs::overlay_run_dirs(kind, token)?;
        Ok(Self { dirs, holder: None })
    }

    /// Allocate overlay dirs under the daemon runtime root and mount them in a
    /// holder-created Host workspace namespace stack.
    ///
    /// # Errors
    ///
    /// Returns [`HostWorkspaceError`] when directory allocation, holder
    /// startup, namespace FD opening, or overlay mounting fails.
    pub fn create_runtime_host_namespace_overlay(
        kind: &str,
        token: &str,
        caller_id: &str,
        workspace_root: &Path,
        layer_paths: &[PathBuf],
        setup_timeout_s: f64,
        exit_grace_s: f64,
    ) -> Result<Self, HostWorkspaceError> {
        let dirs = crate::overlay::dirs::overlay_run_dirs(kind, token)?;
        Self::from_dirs_with_host_namespace(
            dirs,
            token,
            caller_id,
            workspace_root,
            layer_paths,
            setup_timeout_s,
            exit_grace_s,
        )
    }

    /// Allocate overlay dirs under `scratch_root` and mount them in a
    /// holder-created Host workspace namespace stack.
    ///
    /// # Errors
    ///
    /// Returns [`HostWorkspaceError`] when directory allocation, holder
    /// startup, namespace FD opening, or overlay mounting fails.
    pub fn create_with_host_namespace(
        scratch_root: &Path,
        request: HostNamespaceWorkspaceRequest<'_>,
    ) -> Result<Self, HostWorkspaceError> {
        let dirs = allocate_overlay_dirs(scratch_root, request.kind, request.token)?;
        Self::from_dirs_with_host_namespace(
            dirs,
            request.token,
            request.caller_id,
            request.workspace_root,
            request.layer_paths,
            request.setup_timeout_s,
            request.exit_grace_s,
        )
    }

    #[must_use]
    pub fn dirs(&self) -> &OverlayDirs {
        &self.dirs
    }

    #[doc(hidden)]
    #[must_use]
    pub fn namespace_fds(&self) -> Option<WorkspaceNamespaceFds> {
        let holder = self.holder.as_ref()?;
        workspace_namespace_fds_from_map(&holder.handle.ns_fds)
    }

    fn from_dirs_with_host_namespace(
        dirs: OverlayDirs,
        token: &str,
        caller_id: &str,
        workspace_root: &Path,
        layer_paths: &[PathBuf],
        setup_timeout_s: f64,
        exit_grace_s: f64,
    ) -> Result<Self, HostWorkspaceError> {
        let mut workspace = Self { dirs, holder: None };
        workspace.attach_host_namespace(
            token,
            caller_id,
            workspace_root,
            layer_paths,
            setup_timeout_s,
            exit_grace_s,
        )?;
        Ok(workspace)
    }

    fn attach_host_namespace(
        &mut self,
        token: &str,
        caller_id: &str,
        workspace_root: &Path,
        layer_paths: &[PathBuf],
        setup_timeout_s: f64,
        exit_grace_s: f64,
    ) -> Result<(), HostWorkspaceError> {
        let runtime = NamespaceRuntime::from_env();
        let mut handle = new_workspace_handle(WorkspaceHandleSpec {
            workspace_id: WorkspaceModeId(format!("eos-host-{token}")),
            network: NetworkMode::Host,
            caller_id: caller_id.to_owned(),
            lease_id: String::new(),
            manifest_version: 0,
            manifest_root_hash: String::new(),
            workspace_root: workspace_root.to_string_lossy().into_owned(),
            dirs: self.dirs.clone(),
            layer_paths: layer_paths.to_vec(),
            created_at: 0.0,
            last_activity: 0.0,
        });
        let mut profile = HostCompatibleProfile;
        let result = wire_workspace(
            &runtime,
            &mut handle,
            layer_paths,
            setup_timeout_s,
            &mut profile,
        )
        .map(|_| ())
        .map_err(HostWorkspaceError::namespace_setup);
        if let Err(error) = result {
            let _ = teardown_workspace(&runtime, &handle, &mut profile, exit_grace_s);
            return Err(error);
        }
        self.holder = Some(HostHolder {
            handle,
            exit_grace_s,
        });
        Ok(())
    }
}

impl Drop for HostWorkspace {
    fn drop(&mut self) {
        if let Some(holder) = self.holder.take() {
            let mut profile = HostCompatibleProfile;
            let _ = teardown_workspace(
                &NamespaceRuntime::from_env(),
                &holder.handle,
                &mut profile,
                holder.exit_grace_s,
            );
        }
        let _ = std::fs::remove_dir_all(&self.dirs.run_dir);
    }
}

#[cfg(test)]
#[path = "../../tests/unit/host_workspace.rs"]
mod tests;
