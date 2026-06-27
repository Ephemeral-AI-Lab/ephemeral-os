use std::collections::HashMap;
use std::time::Instant;

use crate::lifecycle::leases::{monotonic_seconds, next_handle_id};
use crate::model::{LayerStackSnapshotRef, NetworkProfile, WorkspaceSessionId};
use crate::namespace::NamespacePlan;
use crate::overlay::dirs::create_overlay_dirs;
use crate::session::manager::WorkspaceManagerError;
use crate::session::{validate_workspace_root, MountedWorkspace, WorkspaceManager};

impl WorkspaceManager {
    pub(crate) fn initialize_handle(
        &mut self,
        handle: &mut MountedWorkspace,
    ) -> Result<HashMap<String, f64>, WorkspaceManagerError> {
        let layer_paths = handle.snapshot.layer_paths.clone();
        let namespace_plan = match handle.network {
            NetworkProfile::Shared => NamespacePlan::shared_network(),
            NetworkProfile::Isolated => NamespacePlan::isolated(),
        };
        let mut phases_ms = HashMap::new();

        let mut phase_start = Instant::now();
        handle.holder_pid =
            self.runtime
                .spawn_ns_holder(handle, self.caps.setup_timeout_s, namespace_plan)?;
        super::record_phase_ms(&mut phases_ms, "spawn_ns_holder", phase_start);

        phase_start = Instant::now();
        handle.ns_fds = self
            .runtime
            .open_ns_fds(handle.holder_pid, namespace_plan)?;
        super::record_phase_ms(&mut phases_ms, "open_ns_fds", phase_start);

        if handle.network == NetworkProfile::Isolated {
            self.setup_isolated_network_after_namespace(handle, &mut phases_ms)?;
        }

        phase_start = Instant::now();
        self.runtime.mount_overlay(handle, &layer_paths)?;
        super::record_phase_ms(&mut phases_ms, "mount_overlay", phase_start);

        if handle.network == NetworkProfile::Isolated {
            self.setup_isolated_network_after_mount(handle)?;
        }

        Ok(phases_ms)
    }

    pub(crate) fn rollback_partial(&mut self, handle: &MountedWorkspace) {
        let _ = self.teardown_handle(handle, 1.0);
    }

    fn setup_isolated_network_after_namespace(
        &mut self,
        handle: &mut MountedWorkspace,
        phases_ms: &mut HashMap<String, f64>,
    ) -> Result<(), WorkspaceManagerError> {
        let phase_start = Instant::now();
        self.network.initialize()?;
        let veth = self
            .network
            .install_veth(&handle.workspace_id.0, handle.holder_pid)?;
        handle.veth = Some(veth);
        super::record_phase_ms(phases_ms, "install_veth", phase_start);
        Ok(())
    }

    fn setup_isolated_network_after_mount(
        &mut self,
        handle: &MountedWorkspace,
    ) -> Result<(), WorkspaceManagerError> {
        self.runtime
            .signal_net_ready(handle, self.caps.setup_timeout_s)
    }

    pub fn open(
        &mut self,
        snapshot: LayerStackSnapshotRef,
        network: NetworkProfile,
    ) -> Result<MountedWorkspace, WorkspaceManagerError> {
        let workspace_root = self.validated_workspace_root()?;

        let workspace_id = WorkspaceSessionId(next_handle_id());
        let dirs =
            create_overlay_dirs(self.workspace_session_root(&workspace_id)).map_err(|err| {
                WorkspaceManagerError::SetupFailed {
                    step: format!("create overlay scratch: {err}"),
                }
            })?;

        let now = monotonic_seconds();
        let mut handle = MountedWorkspace {
            workspace_id: workspace_id.clone(),
            network,
            snapshot,
            workspace_root,
            dirs,
            ns_fds: Default::default(),
            holder_pid: 0,
            readiness_fd: -1,
            control_fd: -1,
            veth: None,
            created_at: now,
            last_activity: now,
        };

        if let Err(err) = self.initialize_handle(&mut handle) {
            self.rollback_partial(&handle);
            return Err(err);
        }

        self.handles.insert(workspace_id.clone(), handle.clone());
        if let Err(err) = self.persist_handles() {
            self.handles.remove(&workspace_id);
            self.rollback_partial(&handle);
            return Err(err);
        }
        Ok(handle)
    }

    pub(crate) fn validated_workspace_root(&self) -> Result<String, WorkspaceManagerError> {
        let workspace_root = self.workspace_root.trim();
        validate_workspace_root(workspace_root)?;
        Ok(workspace_root.to_owned())
    }
}
