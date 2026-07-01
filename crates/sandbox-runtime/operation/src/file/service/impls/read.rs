//! `read`: a pure read of a single path. Sessionless reads project the active
//! layerstack snapshot; session reads run a namespace read-window against the
//! session's mounted workspace. Never mounts, publishes, or mutates.

use crate::file::FileService;
use crate::file::{FileOperationError, ReadInput, ReadOutput};
use crate::layerstack::LayerStackService;
use crate::workspace_session::WorkspaceSessionService;

impl FileService {
    /// Read a text window from `input.path`. With `workspace_session_id`, the
    /// read runs inside the live session namespace; without it, the read
    /// projects the latest published snapshot.
    ///
    /// # Errors
    /// Returns [`FileOperationError`] for missing/invalid paths, non-UTF-8 or
    /// non-regular files, oversized selected output, or a backend failure.
    pub fn read(
        &self,
        layerstack: &LayerStackService,
        workspace_session: &WorkspaceSessionService,
        input: ReadInput,
    ) -> Result<ReadOutput, FileOperationError> {
        let _ = (layerstack, workspace_session, &input);
        Err(FileOperationError::WorkspaceSession(
            "file_read backend not yet wired".to_owned(),
        ))
    }
}
