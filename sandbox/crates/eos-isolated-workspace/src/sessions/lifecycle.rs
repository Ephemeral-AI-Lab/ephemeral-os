use std::collections::{HashMap, HashSet};
use std::time::Instant;

use serde_json::{json, Value};

use crate::error::IsolatedError;

use super::resources::{
    close_handle_fds, directory_file_bytes, monotonic_seconds, mountinfo_reference_count,
    next_handle_id,
};
use super::{IsolatedSessions, IsolatedSnapshot, IsolatedWorkspaceId, WorkspaceHandle};

/// A settled exit: everything torn down except the lease, whose `lease_id` the
/// caller releases against the layer stack it acquired from.
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

impl IsolatedSessions {
    pub(super) fn wire_handle(
        &mut self,
        handle: &mut WorkspaceHandle,
    ) -> Result<HashMap<String, f64>, IsolatedError> {
        let mut phases_ms = HashMap::new();
        let mut phase_start = Instant::now();
        handle.holder_pid = self
            .runtime
            .spawn_ns_holder(handle, self.caps.setup_timeout_s)?;
        phases_ms.insert(
            "spawn_ns_holder".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        phase_start = Instant::now();
        handle.ns_fds = self.runtime.open_ns_fds(handle.holder_pid)?;
        phases_ms.insert(
            "open_ns_fds".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        phase_start = Instant::now();
        self.network.initialize()?;
        handle.veth = Some(
            self.network
                .install_veth(&handle.workspace_id.0, handle.holder_pid)?,
        );
        phases_ms.insert(
            "install_veth".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        phase_start = Instant::now();
        self.runtime
            .mount_overlay(handle, &handle.layer_paths.clone())?;
        phases_ms.insert(
            "mount_overlay".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        phase_start = Instant::now();
        let _dns_fallback_applied = self
            .runtime
            .configure_dns(handle, &self.caps.fallback_dns)?;
        phases_ms.insert(
            "configure_dns".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        // signal_net_ready runs UNTIMED between the configure_dns and
        // create_cgroup phase measures (it is deliberately called outside any
        // phase-timer block) so the configure_dns phase budget is not inflated
        // by the net-ready wait.
        self.runtime
            .signal_net_ready(handle, self.caps.setup_timeout_s)?;
        phase_start = Instant::now();
        let cgroup_path = self.runtime.create_cgroup(handle)?;
        phases_ms.insert(
            "create_cgroup".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
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
        let _ = std::fs::remove_dir_all(&handle.scratch_dir);
    }

    pub(super) fn teardown_handle(
        &mut self,
        handle: &WorkspaceHandle,
        grace_s: f64,
    ) -> (Value, HashMap<String, f64>) {
        let mut phases_ms = HashMap::new();
        let phase_start = Instant::now();
        let holder_kill_error = if handle.holder_pid > 0 {
            self.runtime
                .kill_holder(handle.holder_pid, grace_s)
                .err()
                .map(|err| err.to_string())
        } else {
            None
        };
        phases_ms.insert(
            "kill_holder".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        let phase_start = Instant::now();
        close_handle_fds(handle);
        let _close_fds_ms = phase_start.elapsed().as_secs_f64() * 1000.0;
        let phase_start = Instant::now();
        if let Some(veth) = handle.veth.as_ref() {
            self.network.teardown_veth(veth);
        }
        phases_ms.insert(
            "teardown_veth".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        let phase_start = Instant::now();
        if let Some(cgroup_path) = handle.cgroup_path.as_ref() {
            let _ = std::fs::remove_dir(cgroup_path);
        }
        phases_ms.insert(
            "cgroup_rmdir".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        let phase_start = Instant::now();
        let _ = std::fs::remove_dir_all(&handle.scratch_dir);
        phases_ms.insert(
            "rmtree_scratch".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        let cgroup_exists_after = handle.cgroup_path.as_ref().map(|path| path.exists());
        // The lease fields ("lease_released", "active_leases_after") are
        // spliced in by the caller after it releases the returned lease_id.
        let inspection = json!({
            "handle_registered_after": self.handles.contains_key(&handle.workspace_id),
            "agent_registered_after": self.by_caller.contains_key(&handle.caller_id),
            "open_handle_count_after": self.handles.len(),
            "open_agent_count_after": self.by_caller.len(),
            "holder_pid": handle.holder_pid,
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
            "scratch_dir": handle.scratch_dir.to_string_lossy(),
            "scratch_exists_after": handle.scratch_dir.exists(),
            "upperdir_exists_after": handle.upperdir.exists(),
            "workdir_exists_after": handle.workdir.exists(),
            "mountinfo_reference_count_after": mountinfo_reference_count(&[
                &handle.scratch_dir,
                &handle.upperdir,
                &handle.workdir,
            ]),
        });
        (inspection, phases_ms)
    }

    /// Enter (or reject) the isolated workspace for `caller_id` against an
    /// already-acquired `snapshot`.
    ///
    /// Allocates scratch, wires the namespace, and registers the handle. Rolls
    /// back partial state on any wiring failure — the caller releases its
    /// lease when this returns an error.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the feature is disabled, `caller_id` is
    /// invalid, capacity is exhausted, or namespace wiring fails.
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
        let scratch_dir = self.session_scratch_root().join(&workspace_id.0);
        let upperdir = scratch_dir.join("upper");
        let workdir = scratch_dir.join("work");
        std::fs::create_dir_all(&upperdir).map_err(|err| IsolatedError::SetupFailed {
            step: format!("upperdir: {err}"),
        })?;
        std::fs::create_dir_all(&workdir).map_err(|err| IsolatedError::SetupFailed {
            step: format!("workdir: {err}"),
        })?;

        let now = monotonic_seconds();
        let mut handle = WorkspaceHandle {
            workspace_id: workspace_id.clone(),
            caller_id: caller_id.to_owned(),
            lease_id: snapshot.lease_id,
            manifest_version: snapshot.manifest_version,
            manifest_root_hash: snapshot.manifest_root_hash,
            workspace_root,
            scratch_dir,
            upperdir,
            workdir,
            layer_paths: snapshot.layer_paths,
            ns_fds: HashMap::new(),
            holder_pid: 0,
            readiness_fd: -1,
            control_fd: -1,
            veth: None,
            cgroup_path: None,
            created_at: now,
            last_activity: now,
        };

        if let Err(err) = self.wire_handle(&mut handle) {
            self.rollback_partial(&handle);
            return Err(err);
        }

        self.by_caller
            .insert(caller_id.to_owned(), workspace_id.clone());
        self.handles.insert(workspace_id, handle.clone());
        let _ = self.persist_handles();
        Ok(handle)
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

    /// Exit the isolated workspace for `caller_id`.
    ///
    /// Tears down namespace/network/cgroup and DISCARDS the upperdir (no
    /// publish). The returned outcome carries the `lease_id` for the caller to
    /// release.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when `caller_id` is invalid or no isolated
    /// workspace is open for the agent.
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
        let upperdir_bytes = directory_file_bytes(&handle.upperdir);
        let (inspection, phases_ms) =
            self.teardown_handle(&handle, grace_s.unwrap_or(self.caps.exit_grace_s));
        let _ = self.persist_handles();
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

    /// Evict idle handles whose last activity exceeds the configured TTL,
    /// returning each eviction's outcome (the caller releases the leases).
    ///
    /// Callers listed in `active_callers` are skipped because the daemon still
    /// owns at least one live command session for them.
    pub fn ttl_sweep(&mut self, active_callers: &HashSet<String>) -> Vec<ExitOutcome> {
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
