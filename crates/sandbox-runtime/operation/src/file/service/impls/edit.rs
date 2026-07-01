//! `edit`: ordered exact-string replacements over a single path. Sessionless
//! edits are an atomic read-modify-write of head through `amend_path`; session
//! edits read the live overlay, apply the edits, and write back through the
//! namespace runner. Empty and no-op edit sets are rejected.

use sandbox_runtime_layerstack::ManifestFileRead;

use crate::file::service::namespace;
use crate::file::service::support::{amend_error, apply_edits, resolve_layer_path, MAX_EDIT_BYTES};
use crate::file::{EditInput, EditOutput, FileEntryKind, FileOperationError, FileService};
use crate::layerstack::LayerStackService;
use crate::workspace_crate::{FileRunnerOp, FileRunnerResult};
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
        match &input.workspace_session_id {
            Some(workspace_session_id) => {
                let (rel, handler) = namespace::resolve_session_path(
                    workspace_session,
                    &input.path,
                    workspace_session_id.clone(),
                )?;
                let path = rel.as_str().to_owned();
                let current = namespace::run_file_op(
                    workspace_session,
                    &handler,
                    &path,
                    FileRunnerOp::ReadFile {
                        rel: path.clone(),
                        max_bytes: MAX_EDIT_BYTES,
                    },
                )?;
                let bytes = match current {
                    FileRunnerResult::ReadFile {
                        existed, bytes_b64, ..
                    } => {
                        if !existed {
                            return Err(FileOperationError::NotFound(path));
                        }
                        namespace::decode_read_file_bytes(&bytes_b64, &path)?
                    }
                    _ => {
                        return Err(FileOperationError::WorkspaceSession(
                            "namespace read-file returned an unexpected result".to_owned(),
                        ))
                    }
                };
                let text = String::from_utf8(bytes)
                    .map_err(|_| FileOperationError::NotUtf8(path.clone()))?;
                let (edited, replacements) = apply_edits(&text, &input.edits, &path)?;
                let write = namespace::run_file_op(
                    workspace_session,
                    &handler,
                    &path,
                    FileRunnerOp::Write {
                        rel: path.clone(),
                        content: edited,
                    },
                )?;
                match write {
                    FileRunnerResult::Write { bytes_written, .. } => Ok(EditOutput {
                        path,
                        edits_applied: input.edits.len(),
                        replacements,
                        bytes_written,
                    }),
                    _ => Err(FileOperationError::WorkspaceSession(
                        "namespace write returned an unexpected result".to_owned(),
                    )),
                }
            }
            None => {
                let workspace_root = layerstack.workspace_root()?;
                let rel = resolve_layer_path(&workspace_root, &input.path)?;
                let path = rel.as_str().to_owned();
                let owner = format!("operation:{}", input.request_id);
                let edits = &input.edits;
                let mut replacements = 0;
                let outcome = layerstack
                    .amend_path(&rel, &owner, MAX_EDIT_BYTES, |read| {
                        let bytes = match read {
                            ManifestFileRead::Absent => {
                                return Err(FileOperationError::NotFound(path.clone()))
                            }
                            ManifestFileRead::Directory => {
                                return Err(FileOperationError::NotRegular {
                                    path: path.clone(),
                                    kind: FileEntryKind::Directory,
                                })
                            }
                            ManifestFileRead::Symlink => {
                                return Err(FileOperationError::NotRegular {
                                    path: path.clone(),
                                    kind: FileEntryKind::Symlink,
                                })
                            }
                            ManifestFileRead::TooLarge { size, limit } => {
                                return Err(FileOperationError::FileTooLarge {
                                    path: path.clone(),
                                    size,
                                    limit,
                                })
                            }
                            ManifestFileRead::File { bytes, .. } => bytes,
                        };
                        let text = String::from_utf8(bytes)
                            .map_err(|_| FileOperationError::NotUtf8(path.clone()))?;
                        let (edited, count) = apply_edits(&text, edits, &path)?;
                        replacements = count;
                        Ok(edited.into_bytes())
                    })
                    .map_err(amend_error)?;
                Ok(EditOutput {
                    path,
                    edits_applied: input.edits.len(),
                    replacements,
                    bytes_written: outcome.bytes_written,
                })
            }
        }
    }
}
