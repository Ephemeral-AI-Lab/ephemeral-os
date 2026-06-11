//! File operation routing between isolated and direct workspace backends.

use std::path::PathBuf;

use eos_command_ops::CommandBinding;
use eos_file_ops::{
    edit_file as edit_with_backend, read_file as read_with_backend,
    write_file as write_with_backend, DirectBackend, EditFileOutcome, EditFileRequest,
    FileOpsError, IsolatedBackend, ReadFileOutcome, ReadFileRequest, WriteFileOutcome,
    WriteFileRequest,
};
use thiserror::Error;

use crate::WorkspaceRuntime;

/// Backend route selected for one file operation.
#[derive(Debug, Clone)]
pub enum FileRoute {
    Direct { layer_stack_root: PathBuf },
    Isolated,
}

/// File outcome plus the backend route used to produce it.
pub struct RoutedFileOutcome<T> {
    pub route: FileRoute,
    pub outcome: T,
}

/// Routing context after daemon wire parsing.
pub struct FileOpContext<'a> {
    pub workspace: Option<&'a WorkspaceRuntime>,
    pub caller_id: &'a str,
    pub layer_stack_root: Option<PathBuf>,
}

/// Errors from selecting or executing a routed file operation.
#[derive(Debug, Error)]
pub enum FileOpError {
    #[error("layer_stack_root is required")]
    MissingLayerStackRoot,
    #[error(transparent)]
    File(#[from] FileOpsError),
}

/// Read through the caller's isolated workspace if open, otherwise through the
/// direct layer-stack backend.
///
/// # Errors
///
/// Returns [`FileOpError`] when direct routing lacks a layer-stack root or the
/// selected backend rejects the file request.
pub fn read_file(
    context: FileOpContext<'_>,
    request: ReadFileRequest,
) -> Result<RoutedFileOutcome<ReadFileOutcome>, FileOpError> {
    if let Some((workspace, binding)) = isolated_route(&context) {
        let outcome = read_with_backend(&isolated_backend(&binding), request)?;
        workspace.touch(&binding.caller_id);
        return Ok(RoutedFileOutcome {
            route: FileRoute::Isolated,
            outcome,
        });
    }
    let root = context
        .layer_stack_root
        .ok_or(FileOpError::MissingLayerStackRoot)?;
    let outcome = read_with_backend(&DirectBackend::new(root.clone()), request)?;
    Ok(RoutedFileOutcome {
        route: FileRoute::Direct {
            layer_stack_root: root,
        },
        outcome,
    })
}

/// Write through the caller's isolated workspace if open, otherwise through
/// the direct layer-stack backend.
///
/// # Errors
///
/// Returns [`FileOpError`] when direct routing lacks a layer-stack root or the
/// selected backend rejects the file request.
pub fn write_file(
    context: FileOpContext<'_>,
    request: WriteFileRequest,
) -> Result<RoutedFileOutcome<WriteFileOutcome>, FileOpError> {
    if let Some((workspace, binding)) = isolated_route(&context) {
        let outcome = write_with_backend(&isolated_backend(&binding), request)?;
        workspace.touch(&binding.caller_id);
        return Ok(RoutedFileOutcome {
            route: FileRoute::Isolated,
            outcome,
        });
    }
    let root = context
        .layer_stack_root
        .ok_or(FileOpError::MissingLayerStackRoot)?;
    let outcome = write_with_backend(&DirectBackend::new(root.clone()), request)?;
    Ok(RoutedFileOutcome {
        route: FileRoute::Direct {
            layer_stack_root: root,
        },
        outcome,
    })
}

/// Edit through the caller's isolated workspace if open, otherwise through the
/// direct layer-stack backend.
///
/// # Errors
///
/// Returns [`FileOpError`] when direct routing lacks a layer-stack root or the
/// selected backend rejects the file request.
pub fn edit_file(
    context: FileOpContext<'_>,
    request: EditFileRequest,
) -> Result<RoutedFileOutcome<EditFileOutcome>, FileOpError> {
    if let Some((workspace, binding)) = isolated_route(&context) {
        let outcome = edit_with_backend(&isolated_backend(&binding), request)?;
        workspace.touch(&binding.caller_id);
        return Ok(RoutedFileOutcome {
            route: FileRoute::Isolated,
            outcome,
        });
    }
    let root = context
        .layer_stack_root
        .ok_or(FileOpError::MissingLayerStackRoot)?;
    let outcome = edit_with_backend(&DirectBackend::new(root.clone()), request)?;
    Ok(RoutedFileOutcome {
        route: FileRoute::Direct {
            layer_stack_root: root,
        },
        outcome,
    })
}

fn isolated_route<'a>(
    context: &FileOpContext<'a>,
) -> Option<(&'a WorkspaceRuntime, CommandBinding)> {
    let workspace = context.workspace?;
    let binding = workspace.command_binding_for(context.caller_id)?;
    Some((workspace, binding))
}

fn isolated_backend(binding: &CommandBinding) -> IsolatedBackend {
    IsolatedBackend {
        layer_stack_root: binding.layer_stack_root.clone(),
        workspace_root: binding.workspace_root.clone(),
        upperdir: binding.upperdir.clone(),
        layer_paths: binding.layer_paths.clone(),
        manifest_version: binding.manifest_version,
        manifest_root_hash: binding.manifest_root_hash.clone(),
    }
}
