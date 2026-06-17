use std::collections::HashMap;
use std::path::PathBuf;
use std::time::SystemTime;

use crate::workspace_crate::{
    BaseRevision, CallerId, LayerStackSnapshotRef, LeaseId, WorkspaceHandle, WorkspaceId,
};
use crate::workspace_manager::WorkspaceManagerError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WorkspaceLifecycleState {
    Active,
    Closing,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceSessionHandler {
    pub workspace_id: WorkspaceId,
    pub handle: WorkspaceHandle,
    pub layer_stack_root: PathBuf,
    pub lease_id: LeaseId,
    pub snapshot: LayerStackSnapshotRef,
    pub layer_paths: Vec<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct WorkspaceSession {
    pub workspace_id: WorkspaceId,
    pub caller_id: CallerId,
    pub handle: WorkspaceHandle,
    pub layer_stack_root: PathBuf,
    pub lease_id: LeaseId,
    pub snapshot: LayerStackSnapshotRef,
    pub layer_paths: Vec<PathBuf>,
    pub lifecycle_state: WorkspaceLifecycleState,
    pub created_at: SystemTime,
    pub last_activity: SystemTime,
}

impl WorkspaceSession {
    pub(crate) fn from_handle(handle: WorkspaceHandle, layer_stack_root: PathBuf) -> Self {
        let now = SystemTime::now();
        Self {
            workspace_id: handle.id.clone(),
            caller_id: handle.owner.clone(),
            layer_stack_root,
            lease_id: handle.snapshot.lease_id.clone(),
            layer_paths: handle.snapshot.layer_paths.clone(),
            snapshot: handle.snapshot.clone(),
            handle,
            lifecycle_state: WorkspaceLifecycleState::Active,
            created_at: now,
            last_activity: now,
        }
    }

    pub(crate) fn handler(&self) -> WorkspaceSessionHandler {
        WorkspaceSessionHandler {
            workspace_id: self.workspace_id.clone(),
            handle: self.handle.clone(),
            layer_stack_root: self.layer_stack_root.clone(),
            lease_id: self.lease_id.clone(),
            snapshot: self.snapshot.clone(),
            layer_paths: self.layer_paths.clone(),
        }
    }

    pub(crate) fn ensure_active(&self) -> Result<(), WorkspaceManagerError> {
        match self.lifecycle_state {
            WorkspaceLifecycleState::Active => Ok(()),
            WorkspaceLifecycleState::Closing => Err(WorkspaceManagerError::Closing {
                workspace_id: self.workspace_id.clone(),
            }),
        }
    }

    pub(crate) fn active_handle(&self) -> Result<WorkspaceHandle, WorkspaceManagerError> {
        self.ensure_active()?;
        Ok(self.handle.clone())
    }

    pub(crate) fn mark_closing(&mut self) -> Result<WorkspaceHandle, WorkspaceManagerError> {
        self.ensure_active()?;
        self.lifecycle_state = WorkspaceLifecycleState::Closing;
        self.last_activity = SystemTime::now();
        Ok(self.handle.clone())
    }

    pub(crate) fn mark_active(&mut self) {
        self.lifecycle_state = WorkspaceLifecycleState::Active;
        self.last_activity = SystemTime::now();
    }

    pub(crate) fn refresh_after_capture(&mut self, base_revision: BaseRevision) {
        self.handle.base_revision = base_revision;
        self.handle.snapshot.manifest_version = self.handle.base_revision.version;
        self.handle.snapshot.root_hash = self.handle.base_revision.root_hash.clone();
        self.snapshot = self.handle.snapshot.clone();
        self.lease_id = self.snapshot.lease_id.clone();
        self.layer_paths = self.snapshot.layer_paths.clone();
        self.last_activity = SystemTime::now();
    }

    pub(crate) fn refresh_from_handle(
        &mut self,
        handle: WorkspaceHandle,
    ) -> Result<(), WorkspaceManagerError> {
        if handle.id != self.workspace_id {
            return Err(WorkspaceManagerError::RemountWorkspaceIdMismatch {
                expected: self.workspace_id.clone(),
                actual: handle.id,
            });
        }

        self.caller_id = handle.owner.clone();
        self.lease_id = handle.snapshot.lease_id.clone();
        self.layer_paths = handle.snapshot.layer_paths.clone();
        self.snapshot = handle.snapshot.clone();
        self.handle = handle;
        self.last_activity = SystemTime::now();
        Ok(())
    }
}

#[derive(Debug, Default)]
pub(crate) struct WorkspaceSessionManager {
    sessions: HashMap<WorkspaceId, WorkspaceSession>,
}

impl WorkspaceSessionManager {
    pub(crate) fn insert(
        &mut self,
        session: WorkspaceSession,
    ) -> Result<(), WorkspaceManagerError> {
        let workspace_id = session.workspace_id.clone();
        if self.sessions.contains_key(&workspace_id) {
            return Err(WorkspaceManagerError::DuplicateWorkspaceId { workspace_id });
        }
        self.sessions.insert(workspace_id, session);
        Ok(())
    }

    pub(crate) fn remove(&mut self, workspace_id: &WorkspaceId) -> Option<WorkspaceSession> {
        self.sessions.remove(workspace_id)
    }

    pub(crate) fn find_by_workspace_id(
        &self,
        workspace_id: &WorkspaceId,
    ) -> Option<&WorkspaceSession> {
        self.sessions.get(workspace_id)
    }

    pub(crate) fn find_by_workspace_id_mut(
        &mut self,
        workspace_id: &WorkspaceId,
    ) -> Option<&mut WorkspaceSession> {
        self.sessions.get_mut(workspace_id)
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
    use crate::workspace_crate::{
        BaseRevision, LayerStackSnapshotRef, NetworkMode, WorkspaceHandle,
    };

    use super::*;

    fn handle(workspace_id: &str, caller_id: &str, lease_id: &str) -> WorkspaceHandle {
        let snapshot = LayerStackSnapshotRef {
            lease_id: LeaseId(lease_id.to_owned()),
            manifest_version: 1,
            root_hash: "root".to_owned(),
            layer_paths: vec![PathBuf::from("/lower/one")],
        };
        WorkspaceHandle {
            id: WorkspaceId(workspace_id.to_owned()),
            owner: CallerId(caller_id.to_owned()),
            workspace_root: PathBuf::from("/workspace"),
            network: NetworkMode::Host,
            base_revision: BaseRevision {
                version: 1,
                root_hash: "root".to_owned(),
                layer_count: 1,
            },
            snapshot,
        }
    }

    #[test]
    fn workspace_session_manager_inserts_and_finds_by_workspace_id() {
        let mut manager = WorkspaceSessionManager::default();
        let session = WorkspaceSession::from_handle(
            handle("workspace-1", "caller-1", "lease-1"),
            PathBuf::from("/layers"),
        );

        manager.insert(session).expect("insert workspace session");

        assert_eq!(
            manager
                .find_by_workspace_id(&WorkspaceId("workspace-1".to_owned()))
                .expect("workspace session exists")
                .workspace_id,
            WorkspaceId("workspace-1".to_owned())
        );
    }

    #[test]
    fn workspace_session_manager_derives_caller_lookup_from_primary_map() {
        let mut manager = WorkspaceSessionManager::default();
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
    fn workspace_session_manager_derives_lease_lookup_from_primary_map() {
        let mut manager = WorkspaceSessionManager::default();
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
                .workspace_id,
            WorkspaceId("workspace-1".to_owned())
        );
    }

    #[test]
    fn workspace_session_manager_remove_clears_primary_map() {
        let mut manager = WorkspaceSessionManager::default();
        let workspace_id = WorkspaceId("workspace-1".to_owned());
        manager
            .insert(WorkspaceSession::from_handle(
                handle("workspace-1", "caller-1", "lease-1"),
                PathBuf::from("/layers"),
            ))
            .expect("insert workspace session");

        assert!(manager.remove(&workspace_id).is_some());
        assert!(manager.find_by_workspace_id(&workspace_id).is_none());
        assert!(manager
            .find_by_lease_id(&LeaseId("lease-1".to_owned()))
            .is_none());
    }

    #[test]
    fn workspace_session_manager_rejects_duplicate_workspace_id() {
        let mut manager = WorkspaceSessionManager::default();
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
            .expect_err("duplicate workspace id is rejected");

        assert!(matches!(
            error,
            WorkspaceManagerError::DuplicateWorkspaceId { workspace_id }
                if workspace_id == WorkspaceId("workspace-1".to_owned())
        ));
    }
}
