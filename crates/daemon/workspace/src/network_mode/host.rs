use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::isolated_network::{
    DnsConfiguration, IsolatedNetworkError, WorkspaceModeHandle, WorkspaceModeId,
    WorkspaceRemountState,
};
use crate::model::NetworkMode;
use crate::namespace::{NamespacePlan, NamespaceRuntime};
use crate::overlay::dirs::{allocate_overlay_dirs, OverlayDirs};

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

/// One overlay transaction's scratch dirs.
///
/// Dropping the workspace removes its run directory (best-effort), so the caller
/// can capture the upperdir on success or just drop on cancel/discard.
#[derive(Debug)]
pub struct HostWorkspace {
    dirs: OverlayDirs,
    holder: Option<HostHolder>,
}

#[derive(Debug)]
struct HostHolder {
    holder_pid: i32,
    readiness_fd: i32,
    control_fd: i32,
    ns_fds: HashMap<String, i32>,
    exit_grace_s: f64,
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

impl HostWorkspace {
    /// Allocate fresh overlay dirs under `scratch_root` for one operation.
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
        kind: &str,
        token: &str,
        caller_id: &str,
        workspace_root: &Path,
        layer_paths: &[PathBuf],
        setup_timeout_s: f64,
        exit_grace_s: f64,
    ) -> Result<Self, HostWorkspaceError> {
        let dirs = allocate_overlay_dirs(scratch_root, kind, token)?;
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

    #[must_use]
    pub fn dirs(&self) -> &OverlayDirs {
        &self.dirs
    }

    #[doc(hidden)]
    #[must_use]
    pub fn namespace_fds(&self) -> Option<WorkspaceNamespaceFds> {
        let holder = self.holder.as_ref()?;
        namespace_fds_from_map(&holder.ns_fds)
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
        let namespace_plan = NamespacePlan::host_workspace();
        let mut handle = WorkspaceModeHandle {
            workspace_id: WorkspaceModeId(format!("eos-host-{token}")),
            network: NetworkMode::Host,
            caller_id: caller_id.to_owned(),
            lease_id: String::new(),
            manifest_version: 0,
            manifest_root_hash: String::new(),
            workspace_root: workspace_root.to_string_lossy().into_owned(),
            dirs: self.dirs.clone(),
            layer_paths: layer_paths.to_vec(),
            ns_fds: HashMap::new(),
            holder_pid: 0,
            readiness_fd: -1,
            control_fd: -1,
            veth: None,
            cgroup_path: None,
            dns_configuration: DnsConfiguration::default(),
            remount_state: WorkspaceRemountState::Active,
            created_at: 0.0,
            last_activity: 0.0,
        };
        let result = (|| {
            handle.holder_pid = runtime
                .spawn_ns_holder(&mut handle, setup_timeout_s, namespace_plan)
                .map_err(HostWorkspaceError::namespace_setup)?;
            handle.ns_fds = runtime
                .open_ns_fds(handle.holder_pid, namespace_plan)
                .map_err(HostWorkspaceError::namespace_setup)?;
            runtime
                .mount_overlay(&handle, layer_paths, setup_timeout_s)
                .map_err(HostWorkspaceError::namespace_setup)?;
            Ok(())
        })();
        if let Err(error) = result {
            cleanup_holder(
                &runtime,
                handle.holder_pid,
                handle.readiness_fd,
                handle.control_fd,
                &handle.ns_fds,
                exit_grace_s,
            );
            return Err(error);
        }
        self.holder = Some(HostHolder {
            holder_pid: handle.holder_pid,
            readiness_fd: handle.readiness_fd,
            control_fd: handle.control_fd,
            ns_fds: handle.ns_fds,
            exit_grace_s,
        });
        Ok(())
    }
}

impl Drop for HostWorkspace {
    fn drop(&mut self) {
        if let Some(holder) = self.holder.take() {
            cleanup_holder(
                &NamespaceRuntime::from_env(),
                holder.holder_pid,
                holder.readiness_fd,
                holder.control_fd,
                &holder.ns_fds,
                holder.exit_grace_s,
            );
        }
        let _ = std::fs::remove_dir_all(&self.dirs.run_dir);
    }
}

fn cleanup_holder(
    runtime: &NamespaceRuntime,
    holder_pid: i32,
    readiness_fd: i32,
    control_fd: i32,
    ns_fds: &HashMap<String, i32>,
    exit_grace_s: f64,
) {
    if holder_pid > 0 {
        let _ = runtime.kill_holder(holder_pid, exit_grace_s);
    }
    for fd in ns_fds.values().copied() {
        close_fd(fd);
    }
    close_fd(readiness_fd);
    close_fd(control_fd);
}

fn close_fd(fd: i32) {
    if fd >= 0 {
        let _ = nix::unistd::close(fd);
    }
}

fn namespace_fds_from_map(map: &HashMap<String, i32>) -> Option<WorkspaceNamespaceFds> {
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

#[cfg(test)]
#[path = "../../tests/unit/host_workspace.rs"]
mod tests;
