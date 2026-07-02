//! Session file-op glue: resolve a session and repo path, run one file op
//! through [`WorkspaceSessionService::run_file_op`], and map the runner's
//! results and errors into the shared [`FileOperationError`] vocabulary. The
//! `file` domain never learns `setns`, overlay, or namespace detail.

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use sandbox_runtime_layerstack::LayerPath;

use crate::file::service::support::resolve_layer_path;
use crate::file::{FileEntryKind, FileOperationError};
use crate::workspace_crate::{
    FileRunnerEntryKind, FileRunnerError, FileRunnerOp, FileRunnerResult, WorkspaceSessionId,
};
use crate::workspace_session::{WorkspaceSessionHandler, WorkspaceSessionService};

/// Resolve the session and map `path` to a repo-relative [`LayerPath`] against
/// the session's mounted workspace root.
pub(crate) fn resolve_session_path(
    workspace_session: &WorkspaceSessionService,
    path: &str,
    workspace_session_id: WorkspaceSessionId,
) -> Result<(LayerPath, WorkspaceSessionHandler), FileOperationError> {
    let handler = workspace_session
        .resolve_session(workspace_session_id.clone())
        .map_err(|_| FileOperationError::WorkspaceSessionNotFound(workspace_session_id.0))?;
    let rel = resolve_layer_path(&handler.handle.workspace_root, path)?;
    Ok((rel, handler))
}

/// Run one file op in the session namespace, mapping runner errors to
/// [`FileOperationError`] with `rel` as the error path. The handler is
/// resolved fresh inside the session's admission gate by
/// [`WorkspaceSessionService::run_file_op`]; only the session id crosses.
pub(crate) fn run_file_op(
    workspace_session: &WorkspaceSessionService,
    handler: &WorkspaceSessionHandler,
    rel: &str,
    op: FileRunnerOp,
) -> Result<FileRunnerResult, FileOperationError> {
    match workspace_session.run_file_op(&handler.workspace_session_id, op) {
        Ok(Ok(result)) => Ok(result),
        Ok(Err(error)) => Err(map_runner_error(rel, error)),
        Err(error) => Err(FileOperationError::WorkspaceSession(error.to_string())),
    }
}

/// Decode a `ReadFile` base64 payload into raw bytes.
pub(crate) fn decode_read_file_bytes(
    bytes_b64: &str,
    rel: &str,
) -> Result<Vec<u8>, FileOperationError> {
    STANDARD.decode(bytes_b64).map_err(|error| {
        FileOperationError::WorkspaceSession(format!("{rel}: decode read-file bytes: {error}"))
    })
}

fn map_runner_error(rel: &str, error: FileRunnerError) -> FileOperationError {
    match error {
        FileRunnerError::NotRegular { kind } => FileOperationError::NotRegular {
            path: rel.to_owned(),
            kind: map_kind(kind),
        },
        FileRunnerError::NotUtf8 => FileOperationError::NotUtf8(rel.to_owned()),
        FileRunnerError::FileTooLarge { size, limit } => FileOperationError::FileTooLarge {
            path: rel.to_owned(),
            size,
            limit,
        },
        FileRunnerError::OutputTooLarge { limit } => FileOperationError::OutputTooLarge {
            path: rel.to_owned(),
            limit,
        },
        FileRunnerError::Io { message, .. } => {
            FileOperationError::WorkspaceSession(format!("{rel}: {message}"))
        }
    }
}

fn map_kind(kind: FileRunnerEntryKind) -> FileEntryKind {
    match kind {
        FileRunnerEntryKind::Directory => FileEntryKind::Directory,
        FileRunnerEntryKind::Symlink => FileEntryKind::Symlink,
        FileRunnerEntryKind::Other => FileEntryKind::Other,
    }
}
