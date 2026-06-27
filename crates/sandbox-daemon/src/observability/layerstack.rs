//! Layerstack inventory view: merge in-memory lease state (`observe()`) with the
//! observability leaf reader's disk byte sizes (`sample_layerstack`).

use std::collections::HashMap;

use sandbox_observability::LayerStackBytes;
use sandbox_runtime::StackObservation;
use serde_json::{json, Value};

/// Join per-layer bytes (by id) onto the lease observation and derive each
/// layer's `booked_by` set.
///
/// `booked_by` is not stored: it is the full list of leased layers above a layer
/// in base → newest order (the layers whose mount pulls it in as a base).
pub(crate) fn layerstack_view_value(
    observation: &StackObservation,
    bytes: &LayerStackBytes,
) -> Value {
    let bytes_by_id: HashMap<&str, u64> = bytes
        .layers
        .iter()
        .map(|layer| (layer.layer_id.as_str(), layer.bytes))
        .collect();
    let layers = observation
        .layers
        .iter()
        .enumerate()
        .map(|(index, status)| {
            let layer_id = status.layer.layer_id.as_str();
            let booked_by = observation.layers[index + 1..]
                .iter()
                .filter(|above| above.leased_by_workspaces > 0)
                .map(|above| above.layer.layer_id.as_str())
                .collect::<Vec<_>>();
            json!({
                "layer_id": layer_id,
                "bytes": bytes_by_id.get(layer_id).copied().unwrap_or(0),
                "leased_by_workspaces": status.leased_by_workspaces,
                "booked_by": booked_by,
            })
        })
        .collect::<Vec<_>>();
    json!({
        "view": "layerstack",
        "manifest_version": observation.manifest_version,
        "root_hash": observation.root_hash,
        "active_lease_count": observation.active_lease_count,
        "total_bytes": bytes.total_bytes,
        "layers": layers,
    })
}
