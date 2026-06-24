use crate::observability::RuntimeWorkspaceSnapshot;
use crate::workspace_session::WorkspaceSessionService;

impl WorkspaceSessionService {
    pub(crate) fn snapshot_workspaces(&self) -> (Vec<RuntimeWorkspaceSnapshot>, Vec<String>) {
        let sessions = match self.lock_sessions() {
            Ok(sessions) => sessions,
            Err(error) => return (Vec::new(), vec![error.to_string()]),
        };

        let mut errors = Vec::new();
        let mut snapshots = sessions
            .values()
            .map(|session| {
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
                    profile: session.handle.profile,
                    workspace_root: session.handle.workspace_root.clone(),
                    upperdir,
                    workdir,
                    namespace_fd_count,
                    base_manifest_version: Some(session.handle.base_revision.version),
                    base_root_hash: Some(session.handle.base_revision.root_hash.clone()),
                    layer_count: Some(session.handle.base_revision.layer_count),
                }
            })
            .collect::<Vec<_>>();
        snapshots.sort_by(|left, right| left.workspace_id.0.cmp(&right.workspace_id.0));
        (snapshots, errors)
    }
}
