use sandbox_runtime_layerstack::service::StackObservation;

use crate::layerstack::{LayerStackService, LayerStackServiceError};

impl LayerStackService {
    /// Live per-layer lease breakdown of the active manifest.
    ///
    /// Returns in-memory lease state only (layer ids + per-layer lease counts).
    /// Disk byte sizes are merged in separately by the daemon from the
    /// telemetry reader.
    pub fn observe(&self) -> Result<StackObservation, LayerStackServiceError> {
        sandbox_runtime_layerstack::LayerStack::open(self.layer_stack_root.clone())
            .map_err(|error| LayerStackServiceError::LayerStack {
                operation: "open",
                error,
            })?
            .observe()
            .map_err(|error| LayerStackServiceError::LayerStack {
                operation: "observe",
                error,
            })
    }
}
