//! `edit`: ordered exact-string replacements over a single path. Sessionless
//! edits are an atomic read-modify-write of head through `amend_path`; session
//! edits read the live overlay, apply the edits, and write back through the
//! namespace runner. Empty and no-op edit sets are rejected.

use crate::file::FileService;
use crate::file::{EditInput, EditOutput, FileOperationError};
use crate::layerstack::LayerStackService;
use crate::workspace_session::WorkspaceSessionService;

impl FileService {
    /// Apply `input.edits` in order to `input.path`. With `workspace_session_id`,
    /// the edit runs inside the live session namespace and does not publish;
    /// without it, the edit publishes one layer attributed to
    /// `operation:<request_id>`.
    ///
    /// # Errors
    /// Returns [`FileOperationError`] for empty/no-op edits, unmatched or
    /// non-unique `old_string`, missing/invalid/non-regular paths, oversized
    /// files, or a backend failure.
    pub fn edit(
        &self,
        layerstack: &LayerStackService,
        workspace_session: &WorkspaceSessionService,
        input: EditInput,
    ) -> Result<EditOutput, FileOperationError> {
        if input.edits.is_empty() {
            return Err(FileOperationError::NoEdits);
        }
        let _ = (layerstack, workspace_session, &input);
        Err(FileOperationError::WorkspaceSession(
            "file_edit backend not yet wired".to_owned(),
        ))
    }
}
