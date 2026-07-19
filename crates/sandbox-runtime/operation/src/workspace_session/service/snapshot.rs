use std::path::Path;

use crate::observability::{RuntimeTopologyWorkspaceSnapshot, RuntimeWorkspaceSnapshot};
use crate::workspace_session::WorkspaceSessionService;

impl WorkspaceSessionService {
    pub(crate) fn snapshot_workspaces(&self) -> (Vec<RuntimeWorkspaceSnapshot>, Vec<String>) {
        let _ = self.reconcile_holder_exits();
        let sessions = match self.lock_sessions() {
            Ok(sessions) => sessions,
            Err(error) => return (Vec::new(), vec![error.to_string()]),
        };

        let mut errors = Vec::new();
        let mut snapshots = sessions
            .values()
            .map(|session| {
                let cgroup_path = session.cgroup_path.clone();
                let (workload_cgroup_state, workload_cgroup_reason) =
                    self.workload_cgroup_observation(cgroup_path.as_deref());
                let (upperdir, workdir, namespace_fd_count) = match session.handle.entry() {
                    Ok(entry) => (
                        Some(entry.upperdir),
                        Some(entry.workdir),
                        Some(3 + usize::from(entry.ns_fds.net.is_some())),
                    ),
                    Err(_) => {
                        errors.push(format!(
                            "workspace {} lacks launch material",
                            session.workspace_session_id.0
                        ));
                        (None, None, None)
                    }
                };

                RuntimeWorkspaceSnapshot {
                    workspace_id: session.workspace_session_id.clone(),
                    holder_pid: session.handle.holder_pid,
                    holder_live: self.workspace().holder_is_live(&session.handle),
                    network: session.handle.network,
                    finalize_policy: session.finalize_policy,
                    finalization_state: session.finalization_state,
                    workspace_root: session.handle.workspace_root.clone(),
                    upperdir,
                    workdir,
                    namespace_fd_count,
                    base_root_hash: Some(session.handle.snapshot.root_hash.clone()),
                    layer_count: Some(session.handle.snapshot.layer_paths.len()),
                    layer_ids: session
                        .handle
                        .snapshot
                        .manifest
                        .layers
                        .iter()
                        .rev()
                        .map(|layer| layer.layer_id.clone())
                        .collect(),
                    applied_cgroup_limits: cgroup_path.as_ref().and(self.workload_cgroup_limits()),
                    workload_cgroup_state,
                    workload_cgroup_reason,
                    cgroup_path,
                }
            })
            .collect::<Vec<_>>();
        snapshots.sort_by(|left, right| left.workspace_id.0.cmp(&right.workspace_id.0));
        (snapshots, errors)
    }

    pub(crate) fn snapshot_topology_workspaces(
        &self,
    ) -> (Vec<RuntimeTopologyWorkspaceSnapshot>, Vec<String>) {
        let sessions = match self.lock_sessions() {
            Ok(sessions) => sessions,
            Err(error) => return (Vec::new(), vec![error.to_string()]),
        };
        let mut snapshots = sessions
            .values()
            .map(|session| {
                let cgroup_path = session.cgroup_path.clone();
                let (workload_cgroup_state, workload_cgroup_reason) =
                    self.workload_cgroup_observation(cgroup_path.as_deref());
                RuntimeTopologyWorkspaceSnapshot {
                    workspace_id: session.workspace_session_id.clone(),
                    holder_pid: session.handle.holder_pid,
                    holder_live: self.workspace().holder_is_live(&session.handle),
                    applied_cgroup_limits: cgroup_path.as_ref().and(self.workload_cgroup_limits()),
                    workload_cgroup_state,
                    workload_cgroup_reason,
                    cgroup_path,
                }
            })
            .collect::<Vec<_>>();
        snapshots.sort_by(|left, right| left.workspace_id.0.cmp(&right.workspace_id.0));
        (snapshots, Vec::new())
    }

    fn workload_cgroup_observation(&self, cgroup_path: Option<&Path>) -> (String, Option<String>) {
        match (
            cgroup_path.is_some(),
            self.workload_cgroup_limits().is_some(),
        ) {
            (true, true) => ("applied".to_owned(), None),
            (true, false) => (
                "available_unprofiled".to_owned(),
                Some("workload cgroup limits are not configured".to_owned()),
            ),
            (false, true) => (
                "unsupported".to_owned(),
                self.workload_cgroup_unavailable_reason
                    .clone()
                    .or_else(|| Some("delegated cgroup v2 root is unavailable".to_owned())),
            ),
            (false, false) => (
                "not_configured".to_owned(),
                self.workload_cgroup_unavailable_reason.clone(),
            ),
        }
    }
}
