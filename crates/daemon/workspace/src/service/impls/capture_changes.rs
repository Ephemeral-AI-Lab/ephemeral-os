use std::collections::BTreeMap;

use crate::error::WorkspaceError;
use crate::model::{
    CaptureChangesRequest, CapturedWorkspaceChanges, ChangedPathKind, ProtectedPathDrop,
    WorkspaceHandle,
};
use crate::service::{active_mode_id, snapshot_from_public, WorkspaceRuntimeService};

impl WorkspaceRuntimeService {
    pub fn capture_changes(
        &self,
        handle: &WorkspaceHandle,
        request: CaptureChangesRequest,
    ) -> Result<CapturedWorkspaceChanges, WorkspaceError> {
        if let Some(hooks) = self.hooks() {
            return (hooks.capture_changes)(handle, request);
        }

        let (layer_stack_root, upperdir, spool_dir) = {
            let state = self.lock_state()?;
            let mode_id = active_mode_id(&state, handle)?;
            let layer_stack_root =
                state
                    .layer_stack_roots
                    .get(&mode_id)
                    .cloned()
                    .ok_or_else(|| WorkspaceError::Setup {
                        step: format!("missing layer stack root for workspace {}", handle.id.0),
                    })?;
            let mode_handle =
                state
                    .manager
                    .handles
                    .get(&mode_id)
                    .ok_or_else(|| WorkspaceError::NotOpen {
                        owner: handle.owner.clone(),
                    })?;
            (
                layer_stack_root,
                mode_handle.dirs.upperdir.clone(),
                mode_handle.dirs.run_dir.join("capture-spool"),
            )
        };
        let snapshot = snapshot_from_public(&handle.snapshot);
        let captured = crate::overlay::capture::capture_upperdir_for_snapshot_with_options(
            &layer_stack_root,
            &snapshot,
            &upperdir,
            &spool_dir,
            request.bounds,
        )
        .map_err(|error| WorkspaceError::Capture {
            message: error.to_string(),
        })?;
        let changed_paths = captured
            .captured
            .changes
            .iter()
            .map(|change| change.path().as_str().to_owned())
            .collect::<Vec<_>>();
        let changed_path_kinds = captured
            .captured
            .changes
            .iter()
            .map(|change| {
                (
                    change.path().as_str().to_owned(),
                    ChangedPathKind::from(change),
                )
            })
            .collect::<BTreeMap<_, _>>();
        Ok(CapturedWorkspaceChanges {
            workspace_id: handle.id.clone(),
            base_revision: handle.base_revision.clone(),
            changed_paths,
            changed_path_kinds,
            protected_drops: captured
                .captured
                .protected_drops
                .iter()
                .map(ProtectedPathDrop::from)
                .collect(),
            stats: request.include_stats.then_some(captured.captured.stats),
            changes: captured.captured.changes,
            route_stats: captured.route_stats,
            metadata_path_count: captured.metadata_path_count,
            spool_dir: captured.spool_dir,
        })
    }
}
