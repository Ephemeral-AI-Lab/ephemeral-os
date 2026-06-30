use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::error::LayerStackError;
use crate::fs::{layer_digest_path, read_manifest, resolve_layer_path, write_atomic};
use crate::ACTIVE_MANIFEST_FILE;

use super::layer::WORKSPACE_BASE_LAYER_ID;

pub const WORKSPACE_BINDING_FILE: &str = "workspace.json";

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct WorkspaceBinding {
    pub workspace_root: String,
    pub layer_stack_root: String,
    pub base_root_hash: String,
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

pub(super) fn validate_manifest_for_root(
    stack: &Path,
    binding: &WorkspaceBinding,
) -> Result<(), LayerStackError> {
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
    let mut saw_base = false;
    for layer in &manifest.layers {
        let layer_path = resolve_layer_path(stack, &layer.path);
        if !layer_path.exists() {
            return Err(LayerStackError::WorkspaceBinding(format!(
                "manifest layer path is missing for workspace binding: {}",
                layer_path.display()
            )));
        }
        if layer.layer_id == WORKSPACE_BASE_LAYER_ID {
            saw_base = true;
            let digest_path = layer_digest_path(stack, &layer.layer_id);
            let digest = std::fs::read_to_string(&digest_path).map_err(|error| {
                LayerStackError::WorkspaceBinding(format!(
                    "base layer digest is missing for workspace binding: {}: {error}",
                    digest_path.display()
                ))
            })?;
            if digest.trim() != binding.base_root_hash {
                return Err(LayerStackError::WorkspaceBinding(format!(
                    "base layer digest does not match workspace binding: {} != {}",
                    digest.trim(),
                    binding.base_root_hash
                )));
            }
        }
    }
    if !saw_base {
        return Err(LayerStackError::WorkspaceBinding(format!(
            "active manifest has no base layer for workspace binding: {}",
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
