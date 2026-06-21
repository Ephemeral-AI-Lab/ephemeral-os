use crate::error::WorkspaceError;
use crate::model::ReadonlySnapshotHandle;
use crate::service::support::ensure_absolute;
use crate::service::WorkspaceRuntimeService;

impl WorkspaceRuntimeService {
    pub fn latest_snapshot(&self) -> Result<ReadonlySnapshotHandle, WorkspaceError> {
        if let Some(hooks) = self.hooks() {
            return (hooks.latest_snapshot)();
        }

        let layer_stack_root = self.lock_state()?.layer_stack_root.clone();
        ensure_absolute(&layer_stack_root, "layer_stack_root")?;

        let snapshot = sandbox_runtime_layerstack::service::get_snapshot(&layer_stack_root)
            .map_err(|error| WorkspaceError::SnapshotAcquire {
                source: error.to_string(),
            })?;
        let generation_key = format!("{}:{}", snapshot.manifest_version, snapshot.root_hash);
        Ok(ReadonlySnapshotHandle {
            view_root: layer_stack_root,
            generation_key,
            snapshot: snapshot.into(),
        })
    }
}
