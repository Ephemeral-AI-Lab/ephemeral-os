use std::sync::{Arc, Mutex, MutexGuard};
use std::time::SystemTime;

use crate::workspace::session_manager::{
    WorkspaceLifecycleState, WorkspaceSession, WorkspaceSessionManager,
};
use crate::workspace::WorkspaceManagerError;
use crate::workspace_crate::{
    CallerId, CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, LatestSnapshotRequest, LeaseId,
    ReadonlySnapshotHandle, RemountWorkspaceRequest, WorkspaceId, WorkspaceService,
};

use super::session_manager::WorkspaceSessionHandler;

pub struct WorkspaceManagerService {
    sessions: Mutex<WorkspaceSessionManager>,
    workspace: Arc<dyn WorkspaceService>,
}

impl WorkspaceManagerService {
    #[must_use]
    pub fn new(workspace: Arc<dyn WorkspaceService>) -> Self {
        Self {
            sessions: Mutex::new(WorkspaceSessionManager::default()),
            workspace,
        }
    }

    pub fn create(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError> {
        let handle = self.workspace.create_workspace(request)?;
        let workspace_id = handle.id.clone();
        let session = WorkspaceSession::from_handle(handle.clone());
        let handler = session.handler();

        let insert_result = self
            .lock_sessions()
            .and_then(|mut sessions| sessions.insert(session));
        if let Err(insert_error) = insert_result {
            if let Err(rollback_error) = self
                .workspace
                .destroy_workspace(handle, DestroyWorkspaceRequest::default())
            {
                return Err(WorkspaceManagerError::CreateRollbackFailed {
                    workspace_id,
                    insert_error: Box::new(insert_error),
                    rollback_error,
                });
            }
            return Err(insert_error);
        }

        Ok(handler)
    }

    pub fn resolve(
        &self,
        workspace_id: WorkspaceId,
        caller_id: CallerId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError> {
        let sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id(&workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: workspace_id.clone(),
            })?;

        if session.caller_id != caller_id {
            return Err(WorkspaceManagerError::CallerMismatch {
                workspace_id,
                expected: session.caller_id.clone(),
                actual: caller_id,
            });
        }

        Ok(session.handler())
    }

    pub fn find_by_caller_id(
        &self,
        caller_id: &CallerId,
    ) -> Result<Vec<WorkspaceSessionHandler>, WorkspaceManagerError> {
        let sessions = self.lock_sessions()?;
        Ok(sessions
            .find_by_caller_id(caller_id)
            .into_iter()
            .map(WorkspaceSession::handler)
            .collect())
    }

    pub fn find_by_lease_id(
        &self,
        lease_id: &LeaseId,
    ) -> Result<Option<WorkspaceSessionHandler>, WorkspaceManagerError> {
        let sessions = self.lock_sessions()?;
        Ok(sessions
            .find_by_lease_id(lease_id)
            .map(WorkspaceSession::handler))
    }

    pub fn capture_changes(
        &self,
        handler: &WorkspaceSessionHandler,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceManagerError> {
        let result = self
            .workspace
            .capture_changes(&handler.handle, request)
            .map_err(WorkspaceManagerError::from)?;

        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id_mut(&handler.workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: handler.workspace_id.clone(),
            })?;
        session.handle.base_revision = result.base_revision.clone();
        session.snapshot.manifest_version = result.base_revision.version;
        session.snapshot.root_hash = result.base_revision.root_hash.clone();
        session.last_activity = SystemTime::now();

        Ok(result)
    }

    pub fn remount_workspace(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError> {
        let result = self
            .workspace
            .remount_workspace(&handler.handle, request)
            .map_err(WorkspaceManagerError::from)?;

        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id_mut(&handler.workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: handler.workspace_id.clone(),
            })?;
        session.refresh_from_handle(result.handle);
        Ok(session.handler())
    }

    pub fn destroy(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceManagerError> {
        {
            let mut sessions = self.lock_sessions()?;
            let session = sessions
                .find_by_workspace_id_mut(&handler.workspace_id)
                .ok_or_else(|| WorkspaceManagerError::NotFound {
                    workspace_id: handler.workspace_id.clone(),
                })?;
            session.lifecycle_state = WorkspaceLifecycleState::Closing;
            session.last_activity = SystemTime::now();
        }

        match self.workspace.destroy_workspace(handler.handle, request) {
            Ok(result) => {
                let mut sessions = self.lock_sessions()?;
                sessions.remove(&handler.workspace_id);
                Ok(result)
            }
            Err(error) => {
                let mut sessions = self.lock_sessions()?;
                if let Some(session) = sessions.find_by_workspace_id_mut(&handler.workspace_id) {
                    session.lifecycle_state = WorkspaceLifecycleState::Active;
                    session.last_activity = SystemTime::now();
                }
                Err(WorkspaceManagerError::Workspace(error))
            }
        }
    }

    pub fn latest_snapshot(
        &self,
        request: LatestSnapshotRequest,
    ) -> Result<ReadonlySnapshotHandle, WorkspaceManagerError> {
        self.workspace
            .latest_snapshot(request)
            .map_err(WorkspaceManagerError::from)
    }

    fn lock_sessions(
        &self,
    ) -> Result<MutexGuard<'_, WorkspaceSessionManager>, WorkspaceManagerError> {
        self.sessions
            .lock()
            .map_err(|_| WorkspaceManagerError::LockPoisoned)
    }
}
