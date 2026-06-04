use std::collections::HashMap;
use std::time::Instant;

use crate::audit::AuditSink;
use crate::error::IsolatedError;
use serde_json::{json, Value};

use super::support::{close_handle_fds, maybe_inject_phase, mountinfo_reference_count};
use super::{IsolatedSession, LayerStackSnapshotPort, NamespaceRuntimePort, WorkspaceHandle};

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
}
