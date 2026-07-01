//! `write`: overwrite a single path. Sessionless writes publish one layer
//! atomically through `amend_path` under the layerstack writer lock; session
//! writes land in the session overlay through the namespace runner and are
//! attributed later, on session capture.

use crate::file::FileService;
use crate::file::{FileOperationError, WriteInput, WriteOutput};
use crate::layerstack::LayerStackService;
use crate::workspace_session::WorkspaceSessionService;

impl FileService {
    /// Write `input.content` to `input.path`. With `workspace_session_id`, the
    /// write runs inside the live session namespace and does not publish;
    /// without it, the write publishes one layer attributed to
    /// `operation:<request_id>`.
    ///
    /// # Errors
    /// Returns [`FileOperationError`] for invalid/non-regular paths or a backend
    /// failure.
    pub fn write(
        &self,
        layerstack: &LayerStackService,
        workspace_session: &WorkspaceSessionService,
        input: WriteInput,
    ) -> Result<WriteOutput, FileOperationError> {
        let _ = (layerstack, workspace_session, &input);
        Err(FileOperationError::WorkspaceSession(
            "file_write backend not yet wired".to_owned(),
        ))
    }
}
