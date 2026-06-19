use std::collections::HashMap;

use crate::workspace_crate::WorkspaceId;
#[cfg(test)]
use crate::workspace_crate::{CallerId, LeaseId};
use crate::workspace_session::model::WorkspaceSession;
use crate::workspace_session::WorkspaceSessionError;

#[derive(Debug, Default)]
pub(crate) struct WorkspaceSessionStore {
    sessions: HashMap<WorkspaceId, WorkspaceSession>,
}

impl WorkspaceSessionStore {
    pub(crate) fn insert(
        &mut self,
        session: WorkspaceSession,
    ) -> Result<(), WorkspaceSessionError> {
        let workspace_session_id = session.workspace_session_id.clone();
        if self.sessions.contains_key(&workspace_session_id) {
            return Err(WorkspaceSessionError::DuplicateWorkspaceSessionId {
                workspace_session_id,
            });
        }
        self.sessions.insert(workspace_session_id, session);
        Ok(())
    }

    pub(crate) fn remove(
        &mut self,
        workspace_session_id: &WorkspaceId,
    ) -> Option<WorkspaceSession> {
        self.sessions.remove(workspace_session_id)
    }

    pub(crate) fn find_by_workspace_session_id(
        &self,
        workspace_session_id: &WorkspaceId,
    ) -> Option<&WorkspaceSession> {
        self.sessions.get(workspace_session_id)
    }

    pub(crate) fn find_by_workspace_session_id_mut(
        &mut self,
        workspace_session_id: &WorkspaceId,
    ) -> Option<&mut WorkspaceSession> {
        self.sessions.get_mut(workspace_session_id)
    }

    #[cfg(test)]
    pub(crate) fn find_by_caller_id(&self, caller_id: &CallerId) -> Vec<&WorkspaceSession> {
        self.sessions
            .values()
            .filter(|session| &session.caller_id == caller_id)
            .collect()
    }

    #[cfg(test)]
    pub(crate) fn find_by_lease_id(&self, lease_id: &LeaseId) -> Option<&WorkspaceSession> {
        self.sessions
            .values()
            .find(|session| &session.lease_id == lease_id)
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use crate::workspace_crate::{
        CallerId, LayerStackSnapshotRef, LeaseId, WorkspaceHandle, WorkspaceId, WorkspaceProfile,
    };
    use crate::workspace_session::model::WorkspaceSession;

    use super::*;

    fn handle(workspace_session_id: &str, caller_id: &str, lease_id: &str) -> WorkspaceHandle {
        let snapshot = LayerStackSnapshotRef {
            lease_id: LeaseId(lease_id.to_owned()),
            manifest_version: 1,
            root_hash: "root".to_owned(),
            layer_paths: vec![PathBuf::from("/lower/one")],
        };
        WorkspaceHandle::without_launch_for_test(
            WorkspaceId(workspace_session_id.to_owned()),
            CallerId(caller_id.to_owned()),
            PathBuf::from("/workspace"),
            WorkspaceProfile::HostCompatible,
            snapshot,
        )
    }

    #[test]
    fn workspace_session_store_inserts_and_finds_by_workspace_session_id() {
        let mut manager = WorkspaceSessionStore::default();
        let session = WorkspaceSession::from_handle(
            handle("workspace-1", "caller-1", "lease-1"),
            PathBuf::from("/layers"),
        );

        manager.insert(session).expect("insert workspace session");

        assert_eq!(
            manager
                .find_by_workspace_session_id(&WorkspaceId("workspace-1".to_owned()))
                .expect("workspace session exists")
                .workspace_session_id,
            WorkspaceId("workspace-1".to_owned())
        );
    }

    #[test]
    fn workspace_session_store_derives_caller_lookup_from_primary_map() {
        let mut manager = WorkspaceSessionStore::default();
        manager
            .insert(WorkspaceSession::from_handle(
                handle("workspace-1", "caller-1", "lease-1"),
                PathBuf::from("/layers"),
            ))
            .expect("insert first workspace session");
        manager
            .insert(WorkspaceSession::from_handle(
                handle("workspace-2", "caller-1", "lease-2"),
                PathBuf::from("/layers"),
            ))
            .expect("insert second workspace session");

        let sessions = manager.find_by_caller_id(&CallerId("caller-1".to_owned()));

        assert_eq!(sessions.len(), 2);
    }

    #[test]
    fn workspace_session_store_derives_lease_lookup_from_primary_map() {
        let mut manager = WorkspaceSessionStore::default();
        manager
            .insert(WorkspaceSession::from_handle(
                handle("workspace-1", "caller-1", "lease-1"),
                PathBuf::from("/layers"),
            ))
            .expect("insert workspace session");

        assert_eq!(
            manager
                .find_by_lease_id(&LeaseId("lease-1".to_owned()))
                .expect("lease lookup resolves session")
                .workspace_session_id,
            WorkspaceId("workspace-1".to_owned())
        );
    }

    #[test]
    fn workspace_session_store_remove_clears_primary_map() {
        let mut manager = WorkspaceSessionStore::default();
        let workspace_session_id = WorkspaceId("workspace-1".to_owned());
        manager
            .insert(WorkspaceSession::from_handle(
                handle("workspace-1", "caller-1", "lease-1"),
                PathBuf::from("/layers"),
            ))
            .expect("insert workspace session");

        assert!(manager.remove(&workspace_session_id).is_some());
        assert!(manager
            .find_by_workspace_session_id(&workspace_session_id)
            .is_none());
        assert!(manager
            .find_by_lease_id(&LeaseId("lease-1".to_owned()))
            .is_none());
    }

    #[test]
    fn workspace_session_store_rejects_duplicate_workspace_session_id() {
        let mut manager = WorkspaceSessionStore::default();
        manager
            .insert(WorkspaceSession::from_handle(
                handle("workspace-1", "caller-1", "lease-1"),
                PathBuf::from("/layers"),
            ))
            .expect("insert workspace session");

        let error = manager
            .insert(WorkspaceSession::from_handle(
                handle("workspace-1", "caller-2", "lease-2"),
                PathBuf::from("/layers"),
            ))
            .expect_err("duplicate workspace session id is rejected");

        assert!(matches!(
            error,
            WorkspaceSessionError::DuplicateWorkspaceSessionId { workspace_session_id }
                if workspace_session_id == WorkspaceId("workspace-1".to_owned())
        ));
    }
}
