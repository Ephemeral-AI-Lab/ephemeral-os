use std::collections::HashMap;

use crate::workspace_crate::WorkspaceId;
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

    pub(crate) fn session_mut(
        &mut self,
        workspace_session_id: &WorkspaceId,
    ) -> Result<&mut WorkspaceSession, WorkspaceSessionError> {
        self.find_by_workspace_session_id_mut(workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::NotFound {
                workspace_session_id: workspace_session_id.clone(),
            })
    }
}
