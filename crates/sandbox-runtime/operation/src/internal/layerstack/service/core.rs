use std::path::PathBuf;

use crate::layerstack::LayerStackServiceError;
use crate::workspace_crate::{noop_runtime_metrics_recorder, RuntimeMetricsRecorderHandle};

pub struct LayerStackService {
    pub(crate) layer_stack_root: PathBuf,
    pub(crate) binding: sandbox_runtime_layerstack::WorkspaceBinding,
    pub(crate) metrics: RuntimeMetricsRecorderHandle,
}

impl LayerStackService {
    pub fn new(layer_stack_root: PathBuf) -> Result<Self, LayerStackServiceError> {
        Self::with_metrics_recorder(layer_stack_root, noop_runtime_metrics_recorder())
    }

    pub fn with_metrics_recorder(
        layer_stack_root: PathBuf,
        metrics: RuntimeMetricsRecorderHandle,
    ) -> Result<Self, LayerStackServiceError> {
        let binding = sandbox_runtime_layerstack::require_workspace_binding(&layer_stack_root)
            .map_err(|error| LayerStackServiceError::Init {
                layer_stack_root: layer_stack_root.clone(),
                error: error.to_string(),
            })?;
        Ok(Self {
            layer_stack_root,
            binding,
            metrics,
        })
    }

    #[must_use]
    pub fn layer_stack_root(&self) -> &std::path::Path {
        &self.layer_stack_root
    }

    #[must_use]
    pub fn binding(&self) -> &sandbox_runtime_layerstack::WorkspaceBinding {
        &self.binding
    }

    #[must_use]
    pub(crate) fn metrics(&self) -> &RuntimeMetricsRecorderHandle {
        &self.metrics
    }
}
