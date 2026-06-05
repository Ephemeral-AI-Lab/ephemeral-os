use std::collections::{HashMap, HashSet};
use std::time::Instant;

use crate::audit::AuditSink;
use crate::error::IsolatedError;
use serde_json::{json, Value};

use super::support::{
    close_handle_fds, directory_file_bytes, maybe_inject_phase, monotonic_seconds,
    mountinfo_reference_count, next_handle_id,
};
use super::{
    AgentId, IsolatedSession, LayerStackSnapshotPort, NamespaceRuntimePort, WorkspaceHandle,
    WorkspaceHandleId,
};

impl<S, R, A> IsolatedSession<S, R, A>
where
    S: LayerStackSnapshotPort,
    R: NamespaceRuntimePort,
    A: AuditSink,
{
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
        maybe_inject_phase("install_veth")?;
        handle.veth = Some(
            self.network
                .install_veth(&handle.workspace_handle_id.0, handle.holder_pid)?,
        );
        phases_ms.insert(
            "install_veth".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        phase_start = Instant::now();
        maybe_inject_phase("mount_overlay")?;
        self.runtime.mount_overlay(handle, &handle.layer_paths)?;
        phases_ms.insert(
            "mount_overlay".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        phase_start = Instant::now();
        maybe_inject_phase("configure_dns")?;
        let _dns_fallback_applied = self
            .runtime
            .configure_dns(handle, &self.caps.fallback_dns)?;
        phases_ms.insert(
            "configure_dns".to_owned(),
            phase_start.elapsed().as_secs_f64() * 1000.0,
        );
        // signal_net_ready runs UNTIMED between the configure_dns and
        // create_cgroup phase measures, matching Python
        // workspace_handle_lifecycle.py:189 (called outside any t.measure block)
        // so the configure_dns phase budget is not inflated by the net-ready wait.
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
        let lease_released = self.layer_stack.release_lease(&handle.lease_id).ok();
        phases_ms.insert(
            "release_snapshot".to_owned(),
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
        let inspection = json!({
            "handle_registered_after": self.handles.contains_key(&handle.workspace_handle_id),
            "agent_registered_after": self.by_agent.contains_key(&handle.agent_id),
            "open_handle_count_after": self.handles.len(),
            "open_agent_count_after": self.by_agent.len(),
            "lease_released": lease_released,
            "active_leases_after": self.layer_stack.active_lease_count().ok().flatten(),
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

    /// Enter (or reject) the isolated workspace for `agent_id`.
    ///
    /// Acquires the snapshot/lease, allocates scratch, wires the namespace, and
    /// registers the handle. Rolls back partial state (and releases the lease)
    /// on any wiring failure.
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when the feature is disabled, `agent_id` is
    /// invalid, capacity is exhausted, snapshot acquisition fails, or namespace
    /// wiring fails.
    pub fn enter(&mut self, agent_id: &AgentId) -> Result<WorkspaceHandle, IsolatedError> {
        if !self.caps.enabled {
            return Err(IsolatedError::FeatureDisabled);
        }
        if agent_id.0.trim().is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "agent_id is required".to_owned(),
            ));
        }
        let workspace_root = self.validated_workspace_root()?;
        if self.by_agent.contains_key(agent_id) {
            let existing = self
                .by_agent
                .get(agent_id)
                .and_then(|handle_id| self.handles.get(handle_id))
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

        let workspace_handle_id = WorkspaceHandleId(next_handle_id());
        let snapshot = self
            .layer_stack
            .acquire_snapshot(&format!("isolated-{}", workspace_handle_id.0))?;
        let scratch_dir = self.session_scratch_root().join(&workspace_handle_id.0);
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
            workspace_handle_id: workspace_handle_id.clone(),
            agent_id: agent_id.clone(),
            lease_id: snapshot.lease_id.clone(),
            manifest_version: snapshot.manifest_version,
            manifest_root_hash: snapshot.root_hash.clone(),
            workspace_root,
            scratch_dir,
            upperdir,
            workdir,
            layer_paths: snapshot.layer_paths.clone(),
            ns_fds: HashMap::new(),
            holder_pid: 0,
            readiness_fd: -1,
            control_fd: -1,
            veth: None,
            cgroup_path: None,
            created_at: now,
            last_activity: now,
        };

        let enter_timer = Instant::now();
        let phases_ms = match self.wire_handle(&mut handle) {
            Ok(phases_ms) => phases_ms,
            Err(err) => {
                self.rollback_partial(&handle);
                let _ = self.layer_stack.release_lease(&snapshot.lease_id);
                return Err(err);
            }
        };
        let total_ms = enter_timer.elapsed().as_secs_f64() * 1000.0;

        self.by_agent
            .insert(agent_id.clone(), workspace_handle_id.clone());
        self.handles
            .insert(workspace_handle_id.clone(), handle.clone());
        let _ = self.persist_handles();
        let _ = self.audit.emit(
            "sandbox_isolated_workspace_enter",
            json!({
                "workspace_handle_id": workspace_handle_id.0,
                "agent_id": agent_id.0,
                "manifest_version": handle.manifest_version,
                "manifest_root_hash": handle.manifest_root_hash,
                "lease_id": handle.lease_id,
                "lowerdir_layer_count": handle.layer_paths.len(),
                "workspace_root": handle.workspace_root,
                "upperdir": handle.upperdir.to_string_lossy(),
                "workdir": handle.workdir.to_string_lossy(),
                "veth_host_name": handle.veth.as_ref().map(|veth| veth.host_name.as_str()),
                "veth_ns_name": handle.veth.as_ref().map(|veth| veth.ns_name.as_str()),
                "ns_ip": handle.veth.as_ref().map(|veth| veth.ns_ip.to_string()),
                "tree-copy": false,
                "total_ms": total_ms,
                "phases_ms": phases_ms,
            }),
        );
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

    /// Exit the isolated workspace for `agent_id`.
    ///
    /// Tears down namespace/network/cgroup, releases the lease, and DISCARDS
    /// the upperdir (no publish).
    ///
    /// # Errors
    ///
    /// Returns [`IsolatedError`] when `agent_id` is invalid or no isolated
    /// workspace is open for the agent.
    pub fn exit(
        &mut self,
        agent_id: &AgentId,
        grace_s: Option<f64>,
    ) -> Result<Value, IsolatedError> {
        if agent_id.0.trim().is_empty() {
            return Err(IsolatedError::InvalidArgument(
                "agent_id is required".to_owned(),
            ));
        }
        let Some(handle_id) = self.by_agent.remove(agent_id) else {
            return Err(IsolatedError::NotOpen);
        };
        let Some(handle) = self.handles.remove(&handle_id) else {
            return Err(IsolatedError::NotOpen);
        };
        let timer = Instant::now();
        let upperdir_bytes = directory_file_bytes(&handle.upperdir);
        let (inspection, phases_ms) =
            self.teardown_handle(&handle, grace_s.unwrap_or(self.caps.exit_grace_s));
        let _ = self.persist_handles();
        let lifetime_s = (monotonic_seconds() - handle.created_at).max(0.0);
        let total_ms = timer.elapsed().as_secs_f64() * 1000.0;
        let _ = self.audit.emit(
            "sandbox_isolated_workspace_exit",
            json!({
                "workspace_handle_id": handle.workspace_handle_id.0,
                "agent_id": agent_id.0,
                "reason": "explicit",
                "lifetime_s": lifetime_s,
                "upperdir_bytes_discarded": upperdir_bytes,
                "total_ms": total_ms,
                "phases_ms": phases_ms.clone(),
                "scratch_removed": !handle.scratch_dir.exists(),
                "inspection": inspection,
            }),
        );
        Ok(json!({
            "success": true,
            "evicted_upperdir_bytes": upperdir_bytes,
            "lifetime_s": lifetime_s,
            "total_ms": total_ms,
            "phases_ms": phases_ms,
            "inspection": inspection,
        }))
    }

    /// Evict idle handles whose last activity exceeds the configured TTL.
    ///
    /// Agents listed in `active_agents` are skipped because the daemon still
    /// owns at least one live command session for them.
    pub fn ttl_sweep(&mut self, active_agents: &HashSet<String>) -> usize {
        if self.caps.ttl_s <= 0.0 {
            return 0;
        }
        let now = monotonic_seconds();
        let stale = self
            .handles
            .values()
            .filter(|handle| now - handle.last_activity > self.caps.ttl_s)
            .filter(|handle| !active_agents.contains(&handle.agent_id.0))
            .cloned()
            .collect::<Vec<_>>();
        let mut evicted = 0;
        for handle in stale {
            let Ok(stats) = self.exit(&handle.agent_id, None) else {
                continue;
            };
            let upperdir_bytes = stats
                .get("evicted_upperdir_bytes")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            let lifetime_s = stats
                .get("lifetime_s")
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
            let total_ms = stats.get("total_ms").and_then(Value::as_f64).unwrap_or(0.0);
            let phases_ms = stats.get("phases_ms").cloned().unwrap_or_else(|| json!({}));
            let _ = self.audit.emit(
                "sandbox_isolated_workspace_evicted",
                json!({
                    "workspace_handle_id": handle.workspace_handle_id.0,
                    "agent_id": handle.agent_id.0,
                    "reason": "ttl",
                    "lifetime_s": lifetime_s,
                    "upperdir_bytes_discarded": upperdir_bytes,
                    "total_ms": total_ms,
                    "phases_ms": phases_ms,
                }),
            );
            evicted += 1;
        }
        evicted
    }
}
