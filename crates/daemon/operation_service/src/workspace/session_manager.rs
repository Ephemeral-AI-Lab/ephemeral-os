use std::collections::HashMap;
use std::path::PathBuf;
use std::time::SystemTime;

use crate::workspace::WorkspaceManagerError;
use crate::workspace_crate::{
    CallerId, LayerStackSnapshotRef, LeaseId, WorkspaceHandle, WorkspaceId,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RemountState {
    Active,
    Pending,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkspaceLifecycleState {
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
    pub remount_state: RemountState,
    pub lifecycle_state: WorkspaceLifecycleState,
    pub created_at: SystemTime,
    pub last_activity: SystemTime,
}

impl WorkspaceSession {
    pub(crate) fn from_handle(handle: WorkspaceHandle) -> Self {
        let now = SystemTime::now();
        Self {
            workspace_id: handle.id.clone(),
            caller_id: handle.owner.clone(),
            layer_stack_root: handle.workspace_root.clone(),
            lease_id: handle.snapshot.lease_id.clone(),
            layer_paths: handle.snapshot.layer_paths.clone(),
            snapshot: handle.snapshot.clone(),
            handle,
            remount_state: RemountState::Active,
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

    pub(crate) fn refresh_from_handle(&mut self, handle: WorkspaceHandle) {
        self.workspace_id = handle.id.clone();
        self.caller_id = handle.owner.clone();
        self.lease_id = handle.snapshot.lease_id.clone();
        self.layer_paths = handle.snapshot.layer_paths.clone();
        self.snapshot = handle.snapshot.clone();
        self.handle = handle;
        self.last_activity = SystemTime::now();
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

    pub(crate) fn find_by_caller_id(&self, caller_id: &CallerId) -> Vec<&WorkspaceSession> {
        self.sessions
            .values()
            .filter(|session| &session.caller_id == caller_id)
            .collect()
    }

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
        let session = WorkspaceSession::from_handle(handle("workspace-1", "caller-1", "lease-1"));

        manager.insert(session).unwrap();

        assert_eq!(
            manager
                .find_by_workspace_id(&WorkspaceId("workspace-1".to_owned()))
                .unwrap()
                .workspace_id,
            WorkspaceId("workspace-1".to_owned())
        );
    }

    #[test]
    fn workspace_session_manager_derives_caller_lookup_from_primary_map() {
        let mut manager = WorkspaceSessionManager::default();
        manager
            .insert(WorkspaceSession::from_handle(handle(
                "workspace-1",
                "caller-1",
                "lease-1",
            )))
            .unwrap();
        manager
            .insert(WorkspaceSession::from_handle(handle(
                "workspace-2",
                "caller-1",
                "lease-2",
            )))
            .unwrap();

        let sessions = manager.find_by_caller_id(&CallerId("caller-1".to_owned()));

        assert_eq!(sessions.len(), 2);
    }

    #[test]
    fn workspace_session_manager_derives_lease_lookup_from_primary_map() {
        let mut manager = WorkspaceSessionManager::default();
        manager
            .insert(WorkspaceSession::from_handle(handle(
                "workspace-1",
                "caller-1",
                "lease-1",
            )))
            .unwrap();

        assert_eq!(
            manager
                .find_by_lease_id(&LeaseId("lease-1".to_owned()))
                .unwrap()
                .workspace_id,
            WorkspaceId("workspace-1".to_owned())
        );
    }

    #[test]
    fn workspace_session_manager_remove_clears_primary_map() {
        let mut manager = WorkspaceSessionManager::default();
        let workspace_id = WorkspaceId("workspace-1".to_owned());
        manager
            .insert(WorkspaceSession::from_handle(handle(
                "workspace-1",
                "caller-1",
                "lease-1",
            )))
            .unwrap();

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
            .insert(WorkspaceSession::from_handle(handle(
                "workspace-1",
                "caller-1",
                "lease-1",
            )))
            .unwrap();

        let error = manager
            .insert(WorkspaceSession::from_handle(handle(
                "workspace-1",
                "caller-2",
                "lease-2",
            )))
            .unwrap_err();

        assert!(matches!(
            error,
            WorkspaceManagerError::DuplicateWorkspaceId { workspace_id }
                if workspace_id == WorkspaceId("workspace-1".to_owned())
        ));
    }
}
