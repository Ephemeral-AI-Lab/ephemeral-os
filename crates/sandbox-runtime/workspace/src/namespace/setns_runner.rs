use std::path::PathBuf;

#[cfg(target_os = "linux")]
use crate::isolated_network_setup::{BRIDGE_PREFIX_LEN, GATEWAY};
#[cfg(target_os = "linux")]
use crate::model::WorkspaceHandle;
use crate::session::MountedWorkspace;
use crate::session::WorkspaceManagerError;
#[cfg(target_os = "linux")]
use sandbox_observability_telemetry::record::names;
#[cfg(target_os = "linux")]
use sandbox_runtime_namespace_execution::NamespaceTarget;
use sandbox_runtime_namespace_process::runner::protocol::RunResult;
use serde_json::Value;

#[cfg(target_os = "linux")]
use super::fds::{expect_line, write_all_fd};
#[cfg(target_os = "linux")]
use super::holder::ns_holder_runtime_error;
#[cfg(target_os = "linux")]
use super::setup_error;
use super::NamespaceRuntime;

impl NamespaceRuntime {
    pub(crate) fn mount_overlay(
        &self,
        handle: &MountedWorkspace,
        layer_paths: &[PathBuf],
    ) -> Result<(), WorkspaceManagerError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (&self.engine, &self.obs, handle, layer_paths);
            Ok(())
        }
        #[cfg(target_os = "linux")]
        {
            let mut entry = WorkspaceHandle::from(handle).entry().map_err(setup_error)?;
            entry.layer_paths = layer_paths.to_vec();
            let id = self.engine.allocate_id();
            let mount = self
                .engine
                .mount_overlay(NamespaceTarget::from(entry), id)
                .map_err(setup_error)?;
            self.obs
                .scope(names::NAMESPACE_EXEC_MOUNT_OVERLAY, |_span| {
                    mount.wait().map_err(setup_error)
                })
        }
    }

    /// Launch the staged-switch remount runner in the session's namespaces
    /// (peer of [`Self::mount_overlay`], with the rewritten chain and the
    /// fresh sibling workdir overriding the entry): the raw runner
    /// [`RunResult`] comes back verbatim — its two-boolean report drives the
    /// caller's C5 policy, so exit codes are never mount failures.
    pub(crate) fn remount_overlay(
        &self,
        handle: &MountedWorkspace,
        rewritten_layer_paths: Vec<PathBuf>,
        fresh_workdir: &std::path::Path,
    ) -> Result<RunResult, WorkspaceManagerError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (
                &self.engine,
                &self.obs,
                handle,
                rewritten_layer_paths,
                fresh_workdir,
            );
            Err(WorkspaceManagerError::SetupFailed {
                step: "namespace remount runner is only supported on linux".to_owned(),
            })
        }
        #[cfg(target_os = "linux")]
        {
            let mut entry = WorkspaceHandle::from(handle).entry().map_err(setup_error)?;
            entry.layer_paths = rewritten_layer_paths;
            entry.workdir = fresh_workdir.to_path_buf();
            let id = self.engine.allocate_id();
            let execution = self
                .engine
                .remount_overlay(NamespaceTarget::from(entry), id)
                .map_err(setup_error)?;
            self.obs
                .scope(names::NAMESPACE_EXEC_REMOUNT_OVERLAY, |_span| {
                    execution.wait().map_err(setup_error)
                })
        }
    }

    /// Run a file operation inside a mounted session's namespaces (peer of
    /// [`Self::mount_overlay`]): `setns` into the live overlay via a per-op runner
    /// and return the raw runner [`RunResult`]. Does not mount and does not
    /// mutate the host `upperdir`.
    pub(crate) fn run_file_op(
        &self,
        handle: &MountedWorkspace,
        cgroup_procs_path: Option<PathBuf>,
        args: Value,
    ) -> Result<RunResult, WorkspaceManagerError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (&self.engine, &self.obs, handle, cgroup_procs_path, args);
            Err(WorkspaceManagerError::SetupFailed {
                step: "namespace file runner is only supported on linux".to_owned(),
            })
        }
        #[cfg(target_os = "linux")]
        {
            let entry = WorkspaceHandle::from(handle).entry().map_err(setup_error)?;
            let id = self.engine.allocate_id();
            let execution = self
                .engine
                .run_file_op(NamespaceTarget::from(entry), id, args, cgroup_procs_path)
                .map_err(setup_error)?;
            self.obs.scope(names::NAMESPACE_EXEC_FILE_OP, |_span| {
                execution.wait().map_err(setup_error)
            })
        }
    }

    pub(crate) fn signal_net_ready(
        &self,
        handle: &MountedWorkspace,
        setup_timeout_s: f64,
    ) -> Result<(), WorkspaceManagerError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, setup_timeout_s);
        }
        #[cfg(target_os = "linux")]
        {
            let payload = handle.veth.as_ref().map_or_else(
                || "net-ready\n".to_owned(),
                |veth| {
                    format!(
                        "net-ready {} {} {} {}\n",
                        veth.ns_name, veth.ns_ip, BRIDGE_PREFIX_LEN, GATEWAY
                    )
                },
            );
            write_all_fd(handle.control_fd, payload.as_bytes())?;
            if let Err(error) = expect_line(handle.readiness_fd, b"ready", setup_timeout_s) {
                return Err(ns_holder_runtime_error(
                    error,
                    &handle.holder_registration,
                    &self.holder_supervisor,
                )?);
            }
        }
        Ok(())
    }
}
