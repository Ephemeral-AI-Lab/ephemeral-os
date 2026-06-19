use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard};

use crate::workspace_crate::{
    CallerId, CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, NetworkMode, RemountWorkspaceRequest,
    WorkspaceId, WorkspaceService,
};
use crate::workspace_manager::session_manager::{
    WorkspaceRemountState, WorkspaceSession, WorkspaceSessionManager,
};
use crate::workspace_manager::WorkspaceManagerError;

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
        let layer_stack_root = request.layer_stack_root.clone();
        let handle = self.workspace.create_workspace(request)?;
        let workspace_id = handle.id.clone();
        let session = WorkspaceSession::from_handle(handle.clone(), layer_stack_root);
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

    pub fn create_private_workspace(
        &self,
        caller_id: CallerId,
        workspace_root: PathBuf,
        network: NetworkMode,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError> {
        self.create(CreateWorkspaceRequest {
            caller_id,
            layer_stack_root: workspace_root.clone(),
            workspace_root,
            network,
        })
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
        session.ensure_active()?;

        Ok(session.handler())
    }

    pub fn capture_changes(
        &self,
        handler: &WorkspaceSessionHandler,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceManagerError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id_mut(&handler.workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: handler.workspace_id.clone(),
            })?;
        let handle = session.active_handle()?;
        let result = self.workspace.capture_changes(&handle, request)?;
        session.refresh_after_capture(result.base_revision.clone());

        Ok(result)
    }

    pub fn begin_remount(
        &self,
        workspace_id: WorkspaceId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id_mut(&workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: workspace_id.clone(),
            })?;

        session.begin_remount()
    }

    pub fn apply_remount(
        &self,
        handler: &WorkspaceSessionHandler,
        request: RemountWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceManagerError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id_mut(&handler.workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: handler.workspace_id.clone(),
            })?;
        session.ensure_active()?;
        if !matches!(session.remount_state, WorkspaceRemountState::RemountPending) {
            return Err(WorkspaceManagerError::RemountNotPending {
                workspace_id: handler.workspace_id.clone(),
            });
        }

        let result = match self.workspace.remount_workspace(&session.handle, request) {
            Ok(result) => result,
            Err(error) => {
                let reason = error.to_string();
                let _ = session.block_remount(reason);
                return Err(WorkspaceManagerError::Workspace(error));
            }
        };
        if let Err(error) = session.refresh_from_handle(result.handle) {
            let reason = error.to_string();
            let _ = session.block_remount(reason);
            return Err(error);
        }
        Ok(session.handler())
    }

    pub fn finish_remount(&self, workspace_id: WorkspaceId) -> Result<(), WorkspaceManagerError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id_mut(&workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: workspace_id.clone(),
            })?;

        session.finish_remount()
    }

    pub fn finish_or_block_remount(
        &self,
        workspace_id: WorkspaceId,
        reason: Option<String>,
    ) -> Result<(), WorkspaceManagerError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id_mut(&workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: workspace_id.clone(),
            })?;

        match reason {
            Some(reason) => session.block_remount(reason),
            None => session.finish_remount(),
        }
    }

    #[must_use]
    pub fn is_remount_pending(&self, workspace_id: &WorkspaceId) -> bool {
        self.lock_sessions().is_ok_and(|sessions| {
            sessions
                .find_by_workspace_id(workspace_id)
                .is_some_and(|session| {
                    matches!(session.remount_state, WorkspaceRemountState::RemountPending)
                })
        })
    }

    pub fn remount_state(
        &self,
        workspace_id: &WorkspaceId,
    ) -> Result<WorkspaceRemountState, WorkspaceManagerError> {
        let sessions = self.lock_sessions()?;
        sessions
            .find_by_workspace_id(workspace_id)
            .map(|session| session.remount_state.clone())
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: workspace_id.clone(),
            })
    }

    pub fn destroy(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceManagerError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .find_by_workspace_id_mut(&handler.workspace_id)
            .ok_or_else(|| WorkspaceManagerError::NotFound {
                workspace_id: handler.workspace_id.clone(),
            })?;
        let handle = session.mark_closing()?;

        match self.workspace.destroy_workspace(handle, request) {
            Ok(result) => {
                sessions.remove(&handler.workspace_id);
                Ok(result)
            }
            Err(error) => {
                if let Some(session) = sessions.find_by_workspace_id_mut(&handler.workspace_id) {
                    session.mark_active();
                }
                Err(WorkspaceManagerError::Workspace(error))
            }
        }
    }

    fn lock_sessions(
        &self,
    ) -> Result<MutexGuard<'_, WorkspaceSessionManager>, WorkspaceManagerError> {
        self.sessions
            .lock()
            .map_err(|_| WorkspaceManagerError::LockPoisoned)
    }
}
