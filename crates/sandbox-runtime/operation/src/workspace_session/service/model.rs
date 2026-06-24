use std::path::PathBuf;

use crate::workspace_crate::{BaseRevision, WorkspaceHandle, WorkspaceSessionId};
use crate::workspace_session::WorkspaceSessionError;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceSessionHandler {
    pub workspace_session_id: WorkspaceSessionId,
    pub handle: WorkspaceHandle,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct WorkspaceSession {
    pub workspace_session_id: WorkspaceSessionId,
    pub handle: WorkspaceHandle,
}

impl WorkspaceSession {
    pub(crate) fn from_handle(handle: WorkspaceHandle) -> Self {
        Self {
            workspace_session_id: handle.id.clone(),
            handle,
        }
    }

    pub(crate) fn handler(&self) -> WorkspaceSessionHandler {
        WorkspaceSessionHandler {
            workspace_session_id: self.workspace_session_id.clone(),
            handle: self.handle.clone(),
        }
    }

    pub(crate) fn active_handle(&self) -> Result<WorkspaceHandle, WorkspaceSessionError> {
        Ok(self.handle.clone())
    }

    pub(crate) fn refresh_after_capture(&mut self, base_revision: BaseRevision) {
        self.handle.base_revision = base_revision;
        self.handle.snapshot.manifest_version = self.handle.base_revision.version;
        self.handle.snapshot.root_hash = self.handle.base_revision.root_hash.clone();
    }

    pub(crate) fn refresh_after_publish(
        &mut self,
        base_revision: BaseRevision,
        manifest: sandbox_runtime_layerstack::Manifest,
        layer_paths: Vec<PathBuf>,
    ) {
        self.handle.base_revision = base_revision;
        self.handle.snapshot.manifest_version = self.handle.base_revision.version;
        self.handle.snapshot.root_hash = self.handle.base_revision.root_hash.clone();
        self.handle.snapshot.manifest = manifest;
        self.handle.snapshot.layer_paths = layer_paths;
    }
}
