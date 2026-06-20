use crate::workspace_crate::{CaptureChangesRequest, DestroyWorkspaceRequest};
use crate::workspace_session::model::{
    OneShotSessionFinalization, PublishedSessionChanges, WorkspaceSessionHandler,
};
use crate::workspace_session::{WorkspaceSessionError, WorkspaceSessionService};

impl WorkspaceSessionService {
    pub(crate) fn finalize_one_shot_session(
        &self,
        handler: WorkspaceSessionHandler,
        publish_changes: bool,
    ) -> Result<OneShotSessionFinalization, WorkspaceSessionError> {
        let published = if publish_changes {
            Some(self.publish_session_changes(&handler)?)
        } else {
            None
        };
        let destroy = self.destroy_session(handler, DestroyWorkspaceRequest::default())?;

        Ok(OneShotSessionFinalization { published, destroy })
    }

    fn publish_session_changes(
        &self,
        handler: &WorkspaceSessionHandler,
    ) -> Result<PublishedSessionChanges, WorkspaceSessionError> {
        let captured = self.capture_session_changes(
            handler,
            CaptureChangesRequest {
                include_stats: true,
            },
        )?;
        let publish_result = layerstack::service::publish_changes_to_layerstack(
            layerstack::service::PublishChangesRequest {
                root: &handler.layer_stack_root,
                snapshot_manifest_version: handler.handle.snapshot.manifest_version,
                snapshot_layer_paths: &handler.handle.snapshot.layer_paths,
                changes: &captured.changes,
            },
        )
        .map_err(|error| WorkspaceSessionError::PublishCapturedChanges {
            workspace_session_id: handler.workspace_session_id.clone(),
            error: error.to_string(),
        })?;

        Ok(PublishedSessionChanges {
            changed_paths: captured.changed_paths,
            changed_path_kinds: captured.changed_path_kinds,
            protected_drop_count: captured.protected_drops.len(),
            captured_change_count: captured.changes.len(),
            metadata_path_count: captured.metadata_path_count,
            published_manifest_version: publish_result.published_manifest_version,
        })
    }
}
