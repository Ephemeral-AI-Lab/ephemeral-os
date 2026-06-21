use std::collections::hash_map::Entry;

use crate::workspace_crate::{CreateWorkspaceRequest, DestroyWorkspaceRequest};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

use super::super::model::{WorkspaceSession, WorkspaceSessionHandler};

impl WorkspaceSessionService {
    pub fn create_workspace_session(
        &self,
        request: CreateWorkspaceRequest,
    ) -> Result<WorkspaceSessionHandler, WorkspaceSessionError> {
        let handle = self.workspace().create_workspace(request)?;
        let workspace_session_id = handle.id.clone();
        let session = WorkspaceSession::from_handle(handle.clone());
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

        self.cgroup_monitor().register_session_from_handle(&handle);

        Ok(handler)
    }
}
