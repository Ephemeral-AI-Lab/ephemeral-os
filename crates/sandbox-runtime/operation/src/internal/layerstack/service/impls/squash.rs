use crate::layerstack::{LayerStackService, LayerStackServiceError, SquashLayerStackResult};

use super::publish_changes::{layer_paths, revision_from_manifest};

impl LayerStackService {
    pub fn squash(&self) -> Result<SquashLayerStackResult, LayerStackServiceError> {
        let mut stack = sandbox_runtime_layerstack::LayerStack::open(self.layer_stack_root.clone())
            .map_err(|error| LayerStackServiceError::LayerStack {
                operation: "open",
                error,
            })?;
        let outcome = stack
            .squash()
            .map_err(|error| LayerStackServiceError::LayerStack {
                operation: "squash",
                error,
            })?;
        let Some(manifest) = outcome.manifest else {
            return Ok(SquashLayerStackResult {
                squashed: false,
                revision: None,
                layer_paths: Vec::new(),
                lease_release_error: outcome.lease_release_error.map(|err| err.to_string()),
            });
        };
        Ok(SquashLayerStackResult {
            squashed: true,
            revision: Some(revision_from_manifest(&manifest)),
            layer_paths: layer_paths(&self.layer_stack_root, &manifest),
            lease_release_error: outcome.lease_release_error.map(|err| err.to_string()),
        })
    }
}
