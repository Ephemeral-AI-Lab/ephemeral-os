//! Workspace file op router.

use std::path::PathBuf;

use eos_config::configs::daemon::{MAX_FILE_BYTES, MAX_READ_BYTES};
use eos_operation::file::contract::{EditFileInput, ReadFileInput, ReadFileOutput, WriteFileInput};
use eos_operation::file::{
    edit_file as edit_with_backend, read_file as read_with_backend,
    write_file as write_with_backend, DirectBackend, EditFileOutcome, EditFileRequest,
    FileOpsError, IsolatedBackend, ReadFileOutcome, ReadFileRequest, WriteFileOutcome,
    WriteFileRequest,
};
use eos_workspace::IsolatedWorkspaceBinding;
use serde_json::Value;
use thiserror::Error;

use crate::error::DaemonError;
use crate::{DispatchContext, WorkspaceRuntime};

use super::to_wire_value;

#[derive(Debug, Clone)]
enum FileRoute {
    Direct { layer_stack_root: PathBuf },
    Isolated,
}

struct RoutedFileOutcome<T> {
    route: FileRoute,
    outcome: T,
}

struct FileOpContext<'a> {
    workspace: Option<&'a WorkspaceRuntime>,
    caller_id: &'a str,
    layer_stack_root: Option<PathBuf>,
}

#[derive(Debug, Error)]
enum FileOpError {
    #[error("layer_stack_root is required")]
    MissingLayerStackRoot,
    #[error(transparent)]
    File(#[from] FileOpsError),
}

/// `sandbox.file.read` — shared public read op, routed by active workspace mode.
pub(crate) fn op_read_file(
    input: ReadFileInput,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = ReadFileRequest {
        path: input.path,
        max_read_bytes: context
            .file_limits()
            .map_or(MAX_READ_BYTES, |limits| limits.max_read_bytes),
    };
    let caller_id = input.caller.to_string();
    let routed = route_read_file(
        file_context(input.layer_stack_root, context, &caller_id),
        request,
    )
    .map_err(file_op_error)?;
    let mut outcome = routed.outcome;
    if let FileRoute::Direct { layer_stack_root } = routed.route {
        enrich_direct_timings(&layer_stack_root, &mut outcome.timings, 0);
    }
    Ok(read_response(outcome))
}

/// `sandbox.file.write` — shared public write op, routed by active workspace mode.
pub(crate) fn op_write_file(
    input: WriteFileInput,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = WriteFileRequest {
        path: input.path,
        content: input.content.into_bytes(),
        overwrite: input.overwrite,
        max_file_bytes: context
            .file_limits()
            .map_or(MAX_FILE_BYTES, |limits| limits.max_write_bytes),
    };
    let caller_id = input.caller.to_string();
    let routed = route_write_file(
        file_context(input.layer_stack_root, context, &caller_id),
        request,
    )
    .map_err(file_op_error)?;
    let mut outcome = routed.outcome;
    if let FileRoute::Direct { layer_stack_root } = routed.route {
        enrich_direct_timings(
            &layer_stack_root,
            &mut outcome.core.timings,
            outcome.core.changed_paths.len(),
        );
    }
    Ok(to_wire_value(outcome))
}

/// `sandbox.file.edit` — shared public edit op, routed by active workspace mode.
pub(crate) fn op_edit_file(
    input: EditFileInput,
    context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let request = EditFileRequest {
        path: input.path,
        edits: input.edits,
    };
    let caller_id = input.caller.to_string();
    let routed = route_edit_file(
        file_context(input.layer_stack_root, context, &caller_id),
        request,
    )
    .map_err(file_op_error)?;
    let mut mutation = routed.outcome;
    if let FileRoute::Direct { layer_stack_root } = routed.route {
        enrich_direct_timings(
            &layer_stack_root,
            &mut mutation.core.timings,
            mutation.core.changed_paths.len(),
        );
    }
    Ok(to_wire_value(mutation))
}

fn file_context<'a, 'ctx: 'a>(
    layer_stack_root: Option<PathBuf>,
    context: DispatchContext<'ctx>,
    caller_id: &'a str,
) -> FileOpContext<'a> {
    FileOpContext {
        workspace: context.services().map(|services| &services.workspace),
        caller_id,
        layer_stack_root,
    }
}

fn route_read_file(
    context: FileOpContext<'_>,
    request: ReadFileRequest,
) -> Result<RoutedFileOutcome<ReadFileOutcome>, FileOpError> {
    let direct_request = request.clone();
    route_file_op(
        context,
        |binding| read_with_backend(&isolated_backend(binding), request),
        |root| read_with_backend(&DirectBackend::new(root), direct_request),
    )
}

fn route_write_file(
    context: FileOpContext<'_>,
    request: WriteFileRequest,
) -> Result<RoutedFileOutcome<WriteFileOutcome>, FileOpError> {
    let direct_request = request.clone();
    route_file_op(
        context,
        |binding| write_with_backend(&isolated_backend(binding), request),
        |root| write_with_backend(&DirectBackend::new(root), direct_request),
    )
}

fn route_edit_file(
    context: FileOpContext<'_>,
    request: EditFileRequest,
) -> Result<RoutedFileOutcome<EditFileOutcome>, FileOpError> {
    let direct_request = request.clone();
    route_file_op(
        context,
        |binding| edit_with_backend(&isolated_backend(binding), request),
        |root| edit_with_backend(&DirectBackend::new(root), direct_request),
    )
}

fn route_file_op<T>(
    context: FileOpContext<'_>,
    isolated: impl FnOnce(&IsolatedWorkspaceBinding) -> Result<T, FileOpsError>,
    direct: impl FnOnce(PathBuf) -> Result<T, FileOpsError>,
) -> Result<RoutedFileOutcome<T>, FileOpError> {
    if let Some(workspace) = context.workspace {
        if let Some(binding) = workspace.command_binding_for(context.caller_id) {
            let outcome = isolated(&binding)?;
            workspace.touch(&binding.caller_id);
            return Ok(RoutedFileOutcome {
                route: FileRoute::Isolated,
                outcome,
            });
        }
    }
    let root = context
        .layer_stack_root
        .ok_or(FileOpError::MissingLayerStackRoot)?;
    let outcome = direct(root.clone())?;
    Ok(RoutedFileOutcome {
        route: FileRoute::Direct {
            layer_stack_root: root,
        },
        outcome,
    })
}

fn isolated_backend(binding: &IsolatedWorkspaceBinding) -> IsolatedBackend {
    IsolatedBackend {
        layer_stack_root: binding.layer_stack_root.clone(),
        workspace_root: binding.workspace_root.clone(),
        upperdir: binding.upperdir.clone(),
        layer_paths: binding.layer_paths.clone(),
        manifest_version: binding.manifest_version,
        manifest_root_hash: binding.manifest_root_hash.clone(),
    }
}

fn read_response(outcome: ReadFileOutcome) -> Value {
    to_wire_value(ReadFileOutput {
        workspace_kind: outcome.workspace_kind,
        success: outcome.success,
        content: outcome.content,
        exists: outcome.exists,
        encoding: outcome.encoding,
        timings: outcome.timings,
    })
}

/// Splice the daemon's latest-state resource sample (manifest depth, tree-key
/// seeds, cgroup/process gauges) into a direct file-op response — the wire
/// layer's enrichment, so the file-ops crate stays free of process telemetry.
fn enrich_direct_timings(
    root: &std::path::Path,
    timings: &mut eos_operation::file::WorkspaceTimings,
    changed_path_count: usize,
) {
    if let Ok(manifest) = eos_layerstack::service::active_manifest(root) {
        for (key, value) in crate::response::resource_timings(&manifest, changed_path_count) {
            timings.entry(key).or_insert(value);
        }
    }
}

fn file_op_error(error: FileOpError) -> DaemonError {
    match error {
        FileOpError::MissingLayerStackRoot => {
            DaemonError::InvalidRequest("layer_stack_root is required".to_owned())
        }
        FileOpError::File(error) => DaemonError::InvalidRequest(error.to_string()),
    }
}
