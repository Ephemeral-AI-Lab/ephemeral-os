use std::collections::hash_map::Entry;

use crate::workspace_crate::{
    CallerId, CaptureChangesRequest, CapturedWorkspaceChanges, CreateWorkspaceRequest,
    DestroyWorkspaceRequest, DestroyWorkspaceResult, WorkspaceId,
};
use crate::workspace_session::model::{WorkspaceSession, WorkspaceSessionHandler};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    pub fn create_workspace_session(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let layer_stack_root = request.layer_stack_root.clone();
        let handle = self.workspace().create_workspace(request)?;
        let workspace_session_id = handle.id.clone();
        let session = WorkspaceSession::from_handle(handle.clone(), layer_stack_root);
        let handler = session.handler();

        let insert_result = self.lock_sessions().and_then(|mut sessions| {
            match sessions.entry(workspace_session_id.clone()) {
                Entry::Vacant(entry) => {
                    entry.insert(session);
                    Ok(())
                }
                Entry::Occupied(_) => Err(WorkspaceSessionError::DuplicateWorkspaceSessionId {
                    workspace_session_id: workspace_session_id.clone(),
                }),
            }
        });

        if let Err(insert_error) = insert_result {
            if let Err(rollback_error) = self
                .workspace()
                .destroy_workspace(handle, DestroyWorkspaceRequest::default())
            {
                return Err(WorkspaceSessionError::CreateRollbackFailed {
                    workspace_session_id,
                    insert_error: Box::new(insert_error),
                    rollback_error,
                });
            }
            return Err(insert_error);
        }

        Ok(handler)
    }

    pub fn resolve_session(
        &self,
        workspace_session_id: WorkspaceId,
        caller_id: CallerId,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let sessions = self.lock_sessions()?;
        let session = sessions
            .get(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&workspace_session_id))?;

        if session.handle.owner != caller_id {
            return Err(WorkspaceSessionError::CallerMismatch {
                workspace_session_id,
                expected: session.handle.owner.clone(),
                actual: caller_id,
            });
        }

        Ok(session.handler())
    }

    pub fn capture_session_changes(
        &self,
        handler: &WorkspaceSessionHandler,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get_mut(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&handler.workspace_session_id))?;
        let handle = session.active_handle()?;
        let result = self.workspace().capture_changes(&handle, request)?;
        session.refresh_after_capture(result.base_revision.clone());

        Ok(result)
    }

    pub fn destroy_session(
        &self,
        handler: WorkspaceSessionHandler,
        request: DestroyWorkspaceRequest,
    ) -> Result<DestroyWorkspaceResult, WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get(&handler.workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&handler.workspace_session_id))?;
        let handle = session.active_handle()?;

        match self.workspace().destroy_workspace(handle, request) {
            Ok(result) => {
                sessions.remove(&handler.workspace_session_id);
                Ok(result)
            }
            Err(error) => Err(WorkspaceSessionError::Workspace(error)),
        }
    }
}
