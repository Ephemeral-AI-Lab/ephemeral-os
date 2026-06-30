use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use sandbox_observability::Observer;

use crate::file::FileService;
use crate::layerstack::LayerStackServiceError;

pub struct LayerStackService {
    pub(crate) layer_stack_root: PathBuf,
    pub(crate) obs: Observer,
    pub(crate) file: Arc<FileService>,
    pub(crate) audit_gate: Mutex<()>,
}

impl LayerStackService {
    pub fn new(
        layer_stack_root: PathBuf,
        obs: Observer,
        file: Arc<FileService>,
    ) -> Result<Self, LayerStackServiceError> {
        sandbox_runtime_layerstack::require_workspace_binding(&layer_stack_root).map_err(
            |error| LayerStackServiceError::Init {
                layer_stack_root: layer_stack_root.clone(),
                error: error.to_string(),
            },
        )?;
        Ok(Self {
            layer_stack_root,
            obs,
            file,
            audit_gate: Mutex::new(()),
        })
    }

    #[must_use]
    pub fn layer_stack_root(&self) -> &std::path::Path {
        &self.layer_stack_root
    }
}
