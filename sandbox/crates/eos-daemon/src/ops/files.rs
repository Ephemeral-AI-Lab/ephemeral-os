//! Workspace file op router.

use std::path::PathBuf;

use eos_ephemeral_workspace::EphemeralWorkspaceOps;
#[cfg(target_os = "linux")]
use eos_isolated_workspace::IsolatedWorkspaceOps;
use eos_protocol::models::{MAX_FILE_BYTES, MAX_READ_BYTES};
use eos_workspace_api::{
    EditFileOutcome, EditFileRequest, ReadFileOutcome, ReadFileRequest, SearchReplaceEdit,
    WorkspaceApiError, WorkspaceConflict, WorkspaceFileOps, WorkspaceMode, WriteFileOutcome,
    WriteFileRequest,
};
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use crate::request_args::{require_raw_string, require_string};
use crate::services::workspace::EphemeralFilePorts;
#[cfg(target_os = "linux")]
use crate::services::workspace::IsolatedFilePorts;

/// `api.v1.read_file` — shared public read op, routed by active workspace mode.
pub(crate) fn op_read_file(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = read_request(args, context)?;
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::services::workspace_run::isolated::command_handle_for_args(args) {
        let ports = IsolatedFilePorts::new(handle);
        let outcome = IsolatedWorkspaceOps::new(ports.clone())
            .read_file(request)
            .map_err(workspace_error)?;
        ports.record_read_file();
        return Ok(read_response(outcome));
    }
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let outcome = EphemeralWorkspaceOps::new(EphemeralFilePorts::new(root))
        .read_file(request)
        .map_err(workspace_error)?;
    Ok(read_response(outcome))
}

/// `api.v1.write_file` — shared public write op, routed by active workspace mode.
pub(crate) fn op_write_file(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = write_request(args, context)?;
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::services::workspace_run::isolated::command_handle_for_args(args) {
        let outcome = IsolatedWorkspaceOps::new(IsolatedFilePorts::new(handle))
            .write_file(request)
            .map_err(workspace_error)?;
        return Ok(write_response(outcome));
    }
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let outcome = EphemeralWorkspaceOps::new(EphemeralFilePorts::new(root))
        .write_file(request)
        .map_err(workspace_error)?;
    Ok(write_response(outcome))
}

/// `api.v1.edit_file` — shared public edit op, routed by active workspace mode.
pub(crate) fn op_edit_file(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = edit_request(args)?;
    #[cfg(target_os = "linux")]
    if let Some(handle) = crate::services::workspace_run::isolated::command_handle_for_args(args) {
        let outcome = IsolatedWorkspaceOps::new(IsolatedFilePorts::new(handle))
            .edit_file(request)
            .map_err(workspace_error)?;
        return Ok(edit_response(outcome));
    }
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let outcome = EphemeralWorkspaceOps::new(EphemeralFilePorts::new(root))
        .edit_file(request)
        .map_err(workspace_error)?;
    Ok(edit_response(outcome))
}

fn read_request(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<ReadFileRequest, DaemonError> {
    Ok(ReadFileRequest {
        path: require_string(args, "path")?,
        max_read_bytes: context
            .file_limits()
            .map_or(MAX_READ_BYTES, |limits| limits.max_read_bytes),
    })
}

fn write_request(
    args: &Value,
    context: DispatchContext<'_>,
) -> Result<WriteFileRequest, DaemonError> {
    Ok(WriteFileRequest {
        path: require_string(args, "path")?,
        content: require_raw_string(args, "content")?.into_bytes(),
        overwrite: args
            .get("overwrite")
            .and_then(Value::as_bool)
            .unwrap_or(true),
        max_file_bytes: context
            .file_limits()
            .map_or(MAX_FILE_BYTES, |limits| limits.max_write_bytes),
    })
}

fn edit_request(args: &Value) -> Result<EditFileRequest, DaemonError> {
    let edits = args
        .get("edits")
        .and_then(Value::as_array)
        .ok_or_else(|| DaemonError::InvalidEnvelope("edits must be a list".to_owned()))?;
    let mut parsed = Vec::with_capacity(edits.len());
    for raw in edits {
        let edit: SearchReplaceEdit = serde_json::from_value(raw.clone())
            .map_err(|err| DaemonError::InvalidEnvelope(err.to_string()))?;
        if edit.old_text.is_empty() {
            return Err(DaemonError::InvalidEnvelope(
                "edit anchor old_text must be non-empty".to_owned(),
            ));
        }
        parsed.push(edit);
    }
    Ok(EditFileRequest {
        path: require_string(args, "path")?,
        edits: parsed,
    })
}

fn read_response(outcome: ReadFileOutcome) -> Value {
    json!({
        "success": outcome.success,
        "workspace": mode(outcome.mode),
        "workspace_mode": mode(outcome.mode),
        "content": outcome.content,
        "exists": outcome.exists,
        "encoding": outcome.encoding,
        "timings": outcome.timings,
    })
}

fn write_response(outcome: WriteFileOutcome) -> Value {
    GuardedWireResponse {
        workspace_mode: outcome.mode,
        success: outcome.success,
        published: outcome.published,
        status: outcome.status,
        conflict: outcome.conflict,
        conflict_reason: outcome.conflict_reason,
        changed_paths: outcome.changed_paths,
        changed_path_kinds: outcome.changed_path_kinds,
        mutation_source: outcome.mutation_source,
        timings: outcome.timings,
        applied_edits: None,
    }
    .into_json()
}

fn edit_response(outcome: EditFileOutcome) -> Value {
    GuardedWireResponse {
        workspace_mode: outcome.mode,
        success: outcome.success,
        published: outcome.published,
        status: outcome.status,
        conflict: outcome.conflict,
        conflict_reason: outcome.conflict_reason,
        changed_paths: outcome.changed_paths,
        changed_path_kinds: outcome.changed_path_kinds,
        mutation_source: outcome.mutation_source,
        timings: outcome.timings,
        applied_edits: Some(outcome.applied_edits),
    }
    .into_json()
}

struct GuardedWireResponse {
    workspace_mode: WorkspaceMode,
    success: bool,
    published: bool,
    status: String,
    conflict: Option<WorkspaceConflict>,
    conflict_reason: Option<String>,
    changed_paths: Vec<String>,
    changed_path_kinds: std::collections::BTreeMap<String, String>,
    mutation_source: String,
    timings: eos_workspace_api::WorkspaceTimings,
    applied_edits: Option<i64>,
}

impl GuardedWireResponse {
    fn into_json(self) -> Value {
        let mut response = json!({
            "success": self.success,
            "published": self.published,
            "workspace": mode(self.workspace_mode),
            "workspace_mode": mode(self.workspace_mode),
            "changed_paths": self.changed_paths,
            "changed_path_kinds": self.changed_path_kinds,
            "mutation_source": self.mutation_source,
            "status": self.status,
            "conflict": self.conflict.map(conflict_value),
            "conflict_reason": self.conflict_reason,
            "error": null,
            "timings": self.timings,
        });
        if let Some(applied_edits) = self.applied_edits {
            response["applied_edits"] = json!(applied_edits);
        }
        response
    }
}

fn conflict_value(conflict: WorkspaceConflict) -> Value {
    json!({
        "reason": conflict.reason,
        "conflict_file": conflict.conflict_file,
        "message": conflict.message,
    })
}

fn mode(mode: WorkspaceMode) -> &'static str {
    mode.as_str()
}

fn workspace_error(error: WorkspaceApiError) -> DaemonError {
    DaemonError::InvalidEnvelope(error.to_string())
}
