//! `read`: a pure read of a single path. Sessionless reads project the active
//! layerstack snapshot; session reads run a namespace read-window against the
//! session's mounted workspace. Never mounts, publishes, or mutates.

use crate::file::service::namespace;
use crate::file::service::support::{effective_read_window, resolve_layer_path, MAX_OUTPUT_BYTES};
use crate::file::{FileEntryKind, FileOperationError, FileService, ReadInput, ReadOutput};
use crate::layerstack::{LayerStackService, ManifestReadWindow};
use crate::workspace_crate::{FileRunnerOp, FileRunnerResult};
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
        match &input.workspace_session_id {
            Some(workspace_session_id) => {
                let (rel, handler) = namespace::resolve_session_path(
                    workspace_session,
                    &input.path,
                    workspace_session_id.clone(),
                )?;
                let path = rel.as_str().to_owned();
                let (offset, limit) = effective_read_window(input.offset, input.limit);
                let read = namespace::run_file_op(
                    workspace_session,
                    &handler,
                    &path,
                    FileRunnerOp::ReadWindow {
                        rel: path.clone(),
                        offset,
                        limit,
                        output_cap: MAX_OUTPUT_BYTES,
                    },
                )?;
                match read {
                    FileRunnerResult::ReadWindow {
                        existed,
                        content,
                        start_line,
                        num_lines,
                        total_lines,
                        bytes_read,
                        total_bytes,
                        next_offset,
                        truncated,
                    } => {
                        if !existed {
                            return Err(FileOperationError::NotFound(path));
                        }
                        Ok(ReadOutput {
                            path,
                            content,
                            start_line,
                            num_lines,
                            total_lines,
                            bytes_read,
                            total_bytes,
                            next_offset,
                            truncated,
                        })
                    }
                    _ => Err(FileOperationError::WorkspaceSession(
                        "namespace read returned an unexpected result".to_owned(),
                    )),
                }
            }
            None => {
                let workspace_root = layerstack.workspace_root()?;
                let rel = resolve_layer_path(&workspace_root, &input.path)?;
                let path = rel.as_str().to_owned();
                let (offset, limit) = effective_read_window(input.offset, input.limit);
                match layerstack.read_current_window(&rel, offset, limit, MAX_OUTPUT_BYTES)? {
                    ManifestReadWindow::Absent => Err(FileOperationError::NotFound(path)),
                    ManifestReadWindow::Directory => Err(FileOperationError::NotRegular {
                        path,
                        kind: FileEntryKind::Directory,
                    }),
                    ManifestReadWindow::Symlink => Err(FileOperationError::NotRegular {
                        path,
                        kind: FileEntryKind::Symlink,
                    }),
                    ManifestReadWindow::NotUtf8 => Err(FileOperationError::NotUtf8(path)),
                    ManifestReadWindow::OutputTooLarge { limit } => {
                        Err(FileOperationError::OutputTooLarge { path, limit })
                    }
                    ManifestReadWindow::Text {
                        content,
                        start_line,
                        num_lines,
                        total_lines,
                        bytes_read,
                        total_bytes,
                        next_offset,
                        truncated,
                    } => Ok(ReadOutput {
                        path,
                        content,
                        start_line,
                        num_lines,
                        total_lines,
                        bytes_read,
                        total_bytes,
                        next_offset,
                        truncated,
                    }),
                }
            }
        }
    }
}
