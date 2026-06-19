use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Instant;

use crate::lifecycle::remount::WorkspaceRemountState;
use crate::model::NetworkMode;
use crate::namespace::{HolderKillReport, NamespacePlan, NamespaceRuntime};
use crate::overlay::dirs::OverlayDirs;
use crate::profile::manager::IsolatedNetworkError;
use crate::profile::{WorkspaceModeHandle, WorkspaceModeId};

pub(crate) trait ProfileHooks {
    fn namespace_plan(&self) -> NamespacePlan;

    fn setup_after_namespace(
        &mut self,
        _runtime: &NamespaceRuntime,
        _handle: &mut WorkspaceModeHandle,
        _phases_ms: &mut HashMap<String, f64>,
    ) -> Result<(), IsolatedNetworkError> {
        Ok(())
    }

    fn setup_after_mount(
        &mut self,
        _runtime: &NamespaceRuntime,
        _handle: &mut WorkspaceModeHandle,
        _phases_ms: &mut HashMap<String, f64>,
    ) -> Result<(), IsolatedNetworkError> {
        Ok(())
    }

    fn teardown_environment(
        &mut self,
        _runtime: &NamespaceRuntime,
        _handle: &WorkspaceModeHandle,
        _phases_ms: &mut HashMap<String, f64>,
    ) {
    }
}

pub(crate) struct WorkspaceHandleSpec {
    pub workspace_id: WorkspaceModeId,
    pub network: NetworkMode,
    pub caller_id: String,
    pub lease_id: String,
    pub manifest_version: i64,
    pub manifest_root_hash: String,
    pub workspace_root: String,
    pub dirs: OverlayDirs,
    pub layer_paths: Vec<PathBuf>,
    pub created_at: f64,
    pub last_activity: f64,
}

#[must_use]
pub(crate) fn new_workspace_handle(spec: WorkspaceHandleSpec) -> WorkspaceModeHandle {
    WorkspaceModeHandle {
        workspace_id: spec.workspace_id,
        network: spec.network,
        caller_id: spec.caller_id,
        lease_id: spec.lease_id,
        manifest_version: spec.manifest_version,
        manifest_root_hash: spec.manifest_root_hash,
        workspace_root: spec.workspace_root,
        dirs: spec.dirs,
        layer_paths: spec.layer_paths,
        ns_fds: HashMap::new(),
        holder_pid: 0,
        readiness_fd: -1,
        control_fd: -1,
        veth: None,
        cgroup_path: None,
        dns_configuration: Default::default(),
        remount_state: WorkspaceRemountState::Active,
        created_at: spec.created_at,
        last_activity: spec.last_activity,
    }
}

pub(crate) fn wire_workspace(
    runtime: &NamespaceRuntime,
    handle: &mut WorkspaceModeHandle,
    layer_paths: &[PathBuf],
    setup_timeout_s: f64,
    hooks: &mut impl ProfileHooks,
) -> Result<HashMap<String, f64>, IsolatedNetworkError> {
    let mut phases_ms = HashMap::new();
    let namespace_plan = hooks.namespace_plan();
    let mut phase_start = Instant::now();
    handle.holder_pid = runtime.spawn_ns_holder(handle, setup_timeout_s, namespace_plan)?;
    record_phase_ms(&mut phases_ms, "spawn_ns_holder", phase_start);
    phase_start = Instant::now();
    handle.ns_fds = runtime.open_ns_fds(handle.holder_pid, namespace_plan)?;
    record_phase_ms(&mut phases_ms, "open_ns_fds", phase_start);
    hooks.setup_after_namespace(runtime, handle, &mut phases_ms)?;
    phase_start = Instant::now();
    runtime.mount_overlay(handle, layer_paths, setup_timeout_s)?;
    record_phase_ms(&mut phases_ms, "mount_overlay", phase_start);
    hooks.setup_after_mount(runtime, handle, &mut phases_ms)?;
    Ok(phases_ms)
}

pub(crate) struct TeardownReport {
    pub holder_kill_report: HolderKillReport,
    pub holder_kill_error: Option<String>,
    pub phases_ms: HashMap<String, f64>,
}

pub(crate) fn teardown_workspace(
    runtime: &NamespaceRuntime,
    handle: &WorkspaceModeHandle,
    hooks: &mut impl ProfileHooks,
    grace_s: f64,
) -> TeardownReport {
    let mut phases_ms = HashMap::new();
    let phase_start = Instant::now();
    let (holder_kill_report, holder_kill_error) = if handle.holder_pid > 0 {
        match runtime.kill_holder(handle.holder_pid, grace_s) {
            Ok(report) => (report, None),
            Err(err) => (HolderKillReport::default(), Some(err.to_string())),
        }
    } else {
        (HolderKillReport::default(), None)
    };
    record_phase_ms(&mut phases_ms, "kill_holder", phase_start);
    close_handle_fds(handle);
    hooks.teardown_environment(runtime, handle, &mut phases_ms);
    let phase_start = Instant::now();
    let _ = std::fs::remove_dir_all(&handle.dirs.run_dir);
    record_phase_ms(&mut phases_ms, "rmtree_scratch", phase_start);
    TeardownReport {
        holder_kill_report,
        holder_kill_error,
        phases_ms,
    }
}

pub(crate) fn close_handle_fds(handle: &WorkspaceModeHandle) {
    for fd in handle.ns_fds.values().copied() {
        close_fd(fd);
    }
    close_fd(handle.readiness_fd);
    close_fd(handle.control_fd);
}

pub(crate) fn close_fd(fd: i32) {
    if fd >= 0 {
        let _ = nix::unistd::close(fd);
    }
}

pub(crate) fn record_phase_ms(
    phases_ms: &mut HashMap<String, f64>,
    phase: &str,
    started_at: Instant,
) {
    phases_ms.insert(
        phase.to_owned(),
        started_at.elapsed().as_secs_f64() * 1000.0,
    );
}
