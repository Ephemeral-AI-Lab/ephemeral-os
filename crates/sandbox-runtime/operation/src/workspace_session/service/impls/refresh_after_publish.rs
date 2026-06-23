use crate::workspace_crate::{BaseRevision, WorkspaceSessionId};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    pub fn refresh_after_publish(
        &self,
        workspace_session_id: WorkspaceSessionId,
        base_revision: BaseRevision,
        manifest: sandbox_runtime_layerstack::Manifest,
        layer_paths: Vec<std::path::PathBuf>,
    ) -> Result<(), WorkspaceSessionError> {
        let mut sessions = self.lock_sessions()?;
        let session = sessions
            .get_mut(&workspace_session_id)
            .ok_or_else(|| WorkspaceSessionError::not_found(&workspace_session_id))?;
        session.refresh_after_publish(base_revision, manifest, layer_paths);
        Ok(())
    }
}
