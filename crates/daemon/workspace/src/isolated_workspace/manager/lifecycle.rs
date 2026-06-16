use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::dirs::create_overlay_dirs;
use crate::isolated_workspace::error::IsolatedError;
use crate::isolated_workspace::namespace::HolderKillReport;
use crate::tree::directory_file_bytes;

use super::{
    IsolatedManager, IsolatedSnapshot, IsolatedWorkspaceId, WorkspaceHandle, WorkspaceRemountState,
};
use crate::isolated_workspace::{RemountProbe, RemountedWorkspace};

#[derive(Debug, Clone, PartialEq)]
pub struct ExitOutcome {
    pub workspace_id: IsolatedWorkspaceId,
    pub caller_id: String,
    pub lease_id: String,
    pub evicted_upperdir_bytes: u64,
    pub lifetime_s: f64,
    pub total_ms: f64,
    pub phases_ms: HashMap<String, f64>,
    pub inspection: Value,
}

impl IsolatedManager {
    pub(super) fn wire_handle(
        &mut self,
        handle: &mut WorkspaceHandle,
    ) -> Result<HashMap<String, f64>, IsolatedError> {
        let mut phases_ms = HashMap::new();
        let mut phase_start = Instant::now();
        handle.holder_pid = self
            .runtime
            .spawn_ns_holder(handle, self.caps.setup_timeout_s)?;
        record_phase_ms(&mut phases_ms, "spawn_ns_holder", phase_start);
        phase_start = Instant::now();
        handle.ns_fds = self.runtime.open_ns_fds(handle.holder_pid)?;
        record_phase_ms(&mut phases_ms, "open_ns_fds", phase_start);
        phase_start = Instant::now();
        self.network.initialize()?;
        handle.veth = Some(
            self.network
                .install_veth(&handle.workspace_id.0, handle.holder_pid)?,
        );
        record_phase_ms(&mut phases_ms, "install_veth", phase_start);
        phase_start = Instant::now();
        self.runtime.mount_overlay(
            handle,
            &handle.layer_paths.clone(),
            self.caps.setup_timeout_s,
        )?;
        record_phase_ms(&mut phases_ms, "mount_overlay", phase_start);
        phase_start = Instant::now();
        handle.dns_configuration = self.runtime.configure_dns(
            handle,
            &self.caps.fallback_dns,
            self.caps.setup_timeout_s,
        )?;
        record_phase_ms(&mut phases_ms, "configure_dns", phase_start);
        self.runtime
            .signal_net_ready(handle, self.caps.setup_timeout_s)?;
        phase_start = Instant::now();
        let cgroup_path = self.runtime.create_cgroup(handle)?;
        record_phase_ms(&mut phases_ms, "create_cgroup", phase_start);
        if !cgroup_path.as_os_str().is_empty() {
            handle.cgroup_path = Some(cgroup_path);
        }
        Ok(phases_ms)
    }

    pub(super) fn rollback_partial(&mut self, handle: &WorkspaceHandle) {
        close_handle_fds(handle);
        if let Some(veth) = handle.veth.as_ref() {
            self.network.teardown_veth(veth);
        }
        if handle.holder_pid > 0 {
            let _ = self.runtime.kill_holder(handle.holder_pid, 1.0);
        }
        let _ = std::fs::remove_dir_all(&handle.dirs.run_dir);
    }

    pub(super) fn teardown_handle(
        &mut self,
        handle: &WorkspaceHandle,
        grace_s: f64,
    ) -> (Value, HashMap<String, f64>) {
        let mut phases_ms = HashMap::new();
        let phase_start = Instant::now();
        let (holder_kill_report, holder_kill_error) = if handle.holder_pid > 0 {
            match self.runtime.kill_holder(handle.holder_pid, grace_s) {
                Ok(report) => (report, None),
                Err(err) => (HolderKillReport::default(), Some(err.to_string())),
            }
        } else {
            (HolderKillReport::default(), None)
        };
        record_phase_ms(&mut phases_ms, "kill_holder", phase_start);
        close_handle_fds(handle);
        let phase_start = Instant::now();
        if let Some(veth) = handle.veth.as_ref() {
            self.network.teardown_veth(veth);
        }
        record_phase_ms(&mut phases_ms, "teardown_veth", phase_start);
        let phase_start = Instant::now();
        if let Some(cgroup_path) = handle.cgroup_path.as_ref() {
            let _ = std::fs::remove_dir(cgroup_path);
        }
        record_phase_ms(&mut phases_ms, "cgroup_rmdir", phase_start);
        let phase_start = Instant::now();
        let _ = std::fs::remove_dir_all(&handle.dirs.run_dir);
        record_phase_ms(&mut phases_ms, "rmtree_scratch", phase_start);
        let cgroup_exists_after = handle.cgroup_path.as_ref().map(|path| path.exists());
        let inspection = json!({
            "handle_registered_after": self.handles.contains_key(&handle.workspace_id),
            "agent_registered_after": self.by_caller.contains_key(&handle.caller_id),
            "open_handle_count_after": self.handles.len(),
            "open_agent_count_after": self.by_caller.len(),
            "holder_pid": handle.holder_pid,
            "holder_was_alive": holder_kill_report.holder_was_alive,
            "holder_exit_status": holder_kill_report.exit_status,
            "holder_signal": holder_kill_report.signal,
            "holder_status_raw": holder_kill_report.status_raw,
            "holder_kill_error": holder_kill_error,
            "ns_fd_count": handle.ns_fds.len(),
            "readiness_fd_was_open": handle.readiness_fd >= 0,
            "control_fd_was_open": handle.control_fd >= 0,
            "veth_host_name": handle.veth.as_ref().map(|veth| veth.host_name.as_str()),
            "veth_ns_name": handle.veth.as_ref().map(|veth| veth.ns_name.as_str()),
            "cgroup_path": handle
                .cgroup_path
                .as_ref()
                .map(|path| path.to_string_lossy().into_owned()),
            "cgroup_exists_after": cgroup_exists_after,
            "scratch_dir": handle.dirs.run_dir.to_string_lossy(),
            "scratch_exists_after": handle.dirs.run_dir.exists(),
            "upperdir_exists_after": handle.dirs.upperdir.exists(),
            "workdir_exists_after": handle.dirs.workdir.exists(),
            "mountinfo_reference_count_after": mountinfo_reference_count(&[
                &handle.dirs.run_dir,
                &handle.dirs.upperdir,
                &handle.dirs.workdir,
            ]),
        });
        (inspection, phases_ms)
    }

    pub fn enter(
        &mut self,
        caller_id: &str,
        snapshot: IsolatedSnapshot,
    ) -> Result<WorkspaceHandle, IsolatedError> {
        if !self.caps.enabled {
            return Err(IsolatedError::FeatureDisabled);
        }
        if caller_id.trim().is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "caller_id is required".to_owned(),
            ));
        }
        let workspace_root = self.validated_workspace_root()?;
        if self.by_caller.contains_key(caller_id) {
            let existing = self
                .by_caller
                .get(caller_id)
                .and_then(|workspace_id| self.handles.get(workspace_id))
                .ok_or_else(|| IsolatedError::SetupFailed {
                    step: "agent handle index is inconsistent".to_owned(),
                })?;
            return Err(IsolatedError::AlreadyOpen {
                created_at: existing.created_at,
                last_activity: existing.last_activity,
            });
        }
        let total_cap = usize::try_from(self.caps.total_cap).unwrap_or(usize::MAX);
        if self.handles.len() >= total_cap {
            return Err(IsolatedError::QuotaExceeded {
                total_cap: self.caps.total_cap,
            });
        }
        self.check_host_capacity()?;

        let workspace_id = IsolatedWorkspaceId(next_handle_id());
        let dirs = create_overlay_dirs(self.owned_scratch_root().join(&workspace_id.0)).map_err(
            |err| IsolatedError::SetupFailed {
                step: format!("{}: {}", err.path.display(), err.reason),
            },
        )?;

        let now = monotonic_seconds();
        let mut handle = WorkspaceHandle {
            workspace_id: workspace_id.clone(),
            caller_id: caller_id.to_owned(),
            lease_id: snapshot.lease_id,
            manifest_version: snapshot.manifest_version,
            manifest_root_hash: snapshot.manifest_root_hash,
            workspace_root,
            dirs,
            layer_paths: snapshot.layer_paths,
            ns_fds: HashMap::new(),
            holder_pid: 0,
            readiness_fd: -1,
            control_fd: -1,
            veth: None,
            cgroup_path: None,
            dns_configuration: Default::default(),
            remount_state: WorkspaceRemountState::Active,
            created_at: now,
            last_activity: now,
        };

        if let Err(err) = self.wire_handle(&mut handle) {
            self.rollback_partial(&handle);
            return Err(err);
        }

        self.by_caller
            .insert(caller_id.to_owned(), workspace_id.clone());
        self.handles.insert(workspace_id.clone(), handle.clone());
        if let Err(err) = self.persist_handles() {
            self.by_caller.remove(caller_id);
            self.handles.remove(&workspace_id);
            self.rollback_partial(&handle);
            return Err(err);
        }
        Ok(handle)
    }

    pub fn mark_remount_pending(&mut self, caller_id: &str) -> Result<(), IsolatedError> {
        self.set_remount_state(caller_id, WorkspaceRemountState::Pending)
    }

    pub fn clear_remount_pending(&mut self, caller_id: &str) -> Result<(), IsolatedError> {
        self.set_remount_state(caller_id, WorkspaceRemountState::Active)
    }

    fn set_remount_state(
        &mut self,
        caller_id: &str,
        remount_state: WorkspaceRemountState,
    ) -> Result<(), IsolatedError> {
        if caller_id.trim().is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "caller_id is required".to_owned(),
            ));
        }
        let workspace_id = self
            .by_caller
            .get(caller_id)
            .cloned()
            .ok_or(IsolatedError::NotOpen)?;
        let handle = self
            .handles
            .get_mut(&workspace_id)
            .ok_or(IsolatedError::NotOpen)?;
        if handle.remount_state == remount_state {
            return Ok(());
        }
        handle.remount_state = remount_state;
        handle.last_activity = monotonic_seconds();
        self.persist_handles()
    }

    pub fn remount_with_layers(
        &mut self,
        caller_id: &str,
        layer_paths: Vec<PathBuf>,
        probe: &RemountProbe,
    ) -> Result<RemountedWorkspace, IsolatedError> {
        if caller_id.trim().is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "caller_id is required".to_owned(),
            ));
        }
        if layer_paths.is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "layer_paths must not be empty".to_owned(),
            ));
        }
        let workspace_id = self
            .by_caller
            .get(caller_id)
            .cloned()
            .ok_or(IsolatedError::NotOpen)?;
        let handle = self
            .handles
            .get(&workspace_id)
            .cloned()
            .ok_or(IsolatedError::NotOpen)?;
        let remount = self.runtime.remount_overlay(
            &handle,
            &layer_paths,
            probe,
            self.caps.setup_timeout_s,
        )?;
        if !remount.mount_verified {
            return Err(IsolatedError::SetupFailed {
                step: format!(
                    "remount overlay verification failed: {}",
                    remount.failure_summary()
                ),
            });
        }
        let updated = self
            .handles
            .get_mut(&workspace_id)
            .ok_or(IsolatedError::NotOpen)?;
        updated.layer_paths = layer_paths;
        updated.last_activity = monotonic_seconds();
        let updated = updated.clone();
        self.persist_handles()?;
        Ok(RemountedWorkspace {
            handle: updated,
            remount,
        })
    }

    fn validated_workspace_root(&self) -> Result<String, IsolatedError> {
        let workspace_root = self.caps.eos_workspace_root.trim();
        if workspace_root.is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "eos_workspace_root is required".to_owned(),
            ));
        }
        if !std::path::Path::new(workspace_root).is_absolute() {
            return Err(IsolatedError::InvalidArgument(format!(
                "eos_workspace_root must be absolute: {workspace_root}"
            )));
        }
        Ok(workspace_root.to_owned())
    }

    pub fn exit(
        &mut self,
        caller_id: &str,
        grace_s: Option<f64>,
    ) -> Result<ExitOutcome, IsolatedError> {
        if caller_id.trim().is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "caller_id is required".to_owned(),
            ));
        }
        let Some(workspace_id) = self.by_caller.remove(caller_id) else {
            return Err(IsolatedError::NotOpen);
        };
        let Some(handle) = self.handles.remove(&workspace_id) else {
            return Err(IsolatedError::NotOpen);
        };
        let timer = Instant::now();
        let upperdir_bytes = directory_file_bytes(&handle.dirs.upperdir);
        let (mut inspection, mut phases_ms) =
            self.teardown_handle(&handle, grace_s.unwrap_or(self.caps.exit_grace_s));
        let phase_start = Instant::now();
        let persistence_error = self.persist_handles().err().map(|err| err.to_string());
        record_phase_ms(&mut phases_ms, "persist_handles", phase_start);
        if let (Some(error), Some(object)) = (persistence_error, inspection.as_object_mut()) {
            object.insert("persistence_error".to_owned(), json!(error));
        }
        let lifetime_s = (monotonic_seconds() - handle.created_at).max(0.0);
        Ok(ExitOutcome {
            workspace_id: handle.workspace_id,
            caller_id: handle.caller_id,
            lease_id: handle.lease_id,
            evicted_upperdir_bytes: upperdir_bytes,
            lifetime_s,
            total_ms: timer.elapsed().as_secs_f64() * 1000.0,
            phases_ms,
            inspection,
        })
    }

    pub fn evict_idle_workspaces(&mut self, active_callers: &HashSet<String>) -> Vec<ExitOutcome> {
        if self.caps.ttl_s <= 0.0 {
            return Vec::new();
        }
        let now = monotonic_seconds();
        let stale = self
            .handles
            .values()
            .filter(|handle| now - handle.last_activity > self.caps.ttl_s)
            .filter(|handle| !active_callers.contains(&handle.caller_id))
            .map(|handle| handle.caller_id.clone())
            .collect::<Vec<_>>();
        stale
            .into_iter()
            .filter_map(|caller_id| self.exit(&caller_id, None).ok())
            .collect()
    }
}

fn close_handle_fds(handle: &WorkspaceHandle) {
    for fd in handle.ns_fds.values().copied() {
        if fd >= 0 {
            let _ = nix::unistd::close(fd);
        }
    }
    for fd in [handle.readiness_fd, handle.control_fd] {
        if fd >= 0 {
            let _ = nix::unistd::close(fd);
        }
    }
}

fn record_phase_ms(phases_ms: &mut HashMap<String, f64>, phase: &str, started_at: Instant) {
    phases_ms.insert(
        phase.to_owned(),
        started_at.elapsed().as_secs_f64() * 1000.0,
    );
}

pub(super) fn next_handle_id() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(1);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    let counter = COUNTER.fetch_add(1, Ordering::Relaxed) & 0x00ff_ffff;
    format!("{counter:06x}{nanos:016x}")
}

pub(super) fn monotonic_seconds() -> f64 {
    static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

fn mountinfo_reference_count(paths: &[&Path]) -> Option<usize> {
    let mountinfo = std::fs::read_to_string("/proc/self/mountinfo").ok()?;
    let needles = paths
        .iter()
        .map(|path| path.to_string_lossy().into_owned())
        .filter(|path| !path.is_empty())
        .collect::<Vec<_>>();
    Some(
        mountinfo
            .lines()
            .filter(|line| needles.iter().any(|needle| line.contains(needle)))
            .count(),
    )
}
