use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::error::LayerStackError;
use crate::fs::{read_manifest, write_atomic};
use crate::ACTIVE_MANIFEST_FILE;

pub const WORKSPACE_BINDING_FILE: &str = "workspace.json";

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct WorkspaceBinding {
    pub workspace_root: String,
    pub layer_stack_root: String,
    pub active_manifest_version: i64,
    pub active_root_hash: String,
    pub base_manifest_version: i64,
    pub base_root_hash: String,
}

impl WorkspaceBinding {
    pub fn layer_path_from_relative(&self, path: &str) -> Result<String, LayerStackError> {
        let raw = required_path(path)?;
        if raw.starts_with('/') {
            return Err(LayerStackError::WorkspaceBinding(format!(
                "path must be relative: {raw}"
            )));
        }
        normalize_layer_path(raw)
    }

    pub fn layer_path_from_absolute(&self, path: &str) -> Result<String, LayerStackError> {
        let raw = required_path(path)?;
        if !raw.starts_with('/') {
            return Err(LayerStackError::WorkspaceBinding(format!(
                "path must be absolute: {raw}"
            )));
        }
        let workspace = PathBuf::from(&self.workspace_root);
        let candidate = PathBuf::from(raw);
        let relative = candidate.strip_prefix(&workspace).map_err(|_| {
            LayerStackError::WorkspaceBinding(format!(
                "path is outside bound workspace {}: {raw}",
                self.workspace_root
            ))
        })?;
        normalize_layer_path(&relative.to_string_lossy())
    }
}

pub fn read_workspace_binding(
    layer_stack_root: impl AsRef<Path>,
) -> Result<Option<WorkspaceBinding>, LayerStackError> {
    let path = layer_stack_root.as_ref().join(WORKSPACE_BINDING_FILE);
    if !path.exists() {
        return Ok(None);
    }
    let payload = std::fs::read_to_string(&path)?;
    let binding = serde_json::from_str::<WorkspaceBinding>(&payload)
        .map_err(|err| LayerStackError::WorkspaceBinding(err.to_string()))?;
    Ok(Some(binding))
}

pub fn require_workspace_binding(
    layer_stack_root: impl AsRef<Path>,
) -> Result<WorkspaceBinding, LayerStackError> {
    read_workspace_binding(layer_stack_root.as_ref())?.ok_or_else(|| {
        LayerStackError::WorkspaceBinding(format!(
            "workspace binding is missing: {}",
            layer_stack_root
                .as_ref()
                .join(WORKSPACE_BINDING_FILE)
                .display()
        ))
    })
}

pub(super) fn validate_manifest_for_root(stack: &Path) -> Result<(), LayerStackError> {
    let manifest_file = stack.join(ACTIVE_MANIFEST_FILE);
    if !manifest_file.exists() {
        return Err(LayerStackError::WorkspaceBinding(format!(
            "active manifest is missing for workspace binding: {}",
            manifest_file.display()
        )));
    }
    let manifest = read_manifest(manifest_file)?;
    if manifest.version <= 0 || manifest.layers.is_empty() {
        return Err(LayerStackError::WorkspaceBinding(format!(
            "active manifest is empty for workspace binding: {}",
            stack.join(ACTIVE_MANIFEST_FILE).display()
        )));
    }
    Ok(())
}

pub(super) fn validate_workspace_binding_paths(
    workspace: &Path,
    stack: &Path,
) -> Result<(), LayerStackError> {
    if !workspace.is_absolute() {
        return Err(LayerStackError::WorkspaceBinding(format!(
            "workspace_root must be absolute: {}",
            workspace.display()
        )));
    }
    if !stack.is_absolute() {
        return Err(LayerStackError::WorkspaceBinding(format!(
            "layer_stack_root must be absolute: {}",
            stack.display()
        )));
    }
    if stack == workspace || stack.starts_with(workspace) {
        return Err(LayerStackError::WorkspaceBinding(format!(
            "layer_stack_root must be outside workspace_root: {} is inside {}",
            stack.display(),
            workspace.display()
        )));
    }
    Ok(())
}

pub(super) fn write_workspace_binding_at(
    target_stack: &Path,
    binding: &WorkspaceBinding,
) -> Result<(), LayerStackError> {
    validate_workspace_binding_paths(
        Path::new(&binding.workspace_root),
        Path::new(&binding.layer_stack_root),
    )?;
    let encoded = serde_json::to_vec_pretty(binding)
        .map_err(|err| LayerStackError::WorkspaceBinding(err.to_string()))?;
    write_atomic(target_stack.join(WORKSPACE_BINDING_FILE), &encoded)
}

fn required_path(path: &str) -> Result<&str, LayerStackError> {
    let raw = path.trim();
    if raw.is_empty() {
        return Err(LayerStackError::WorkspaceBinding(
            "path is required".to_owned(),
        ));
    }
    Ok(raw)
}

fn normalize_layer_path(path: &str) -> Result<String, LayerStackError> {
    crate::model::LayerPath::parse(path)
        .map(|path| path.as_str().to_owned())
        .map_err(LayerStackError::from)
}
