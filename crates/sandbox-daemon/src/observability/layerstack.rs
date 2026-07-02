//! Layerstack inventory view: merge in-memory lease state (`observe()`) with the
//! observability leaf reader's disk byte sizes (`sample_layerstack`).

use std::collections::HashMap;

use sandbox_observability::LayerStackBytes;
use sandbox_runtime::{RuntimeWorkspaceSnapshot, StackObservation};
use serde_json::{json, Value};

/// Join per-layer bytes (by id) onto the lease observation and derive each
/// layer's `booked_by` set.
///
/// `booked_by` is not stored: it is the full list of leased layers above a layer
/// in newest → base order (the layers whose mount pulls it in as a base).
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
            let booked_by = observation.layers[..index]
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

/// The one-line stack summary grafted onto the `snapshot` view:
/// `N layers  <bytes>  K leases`.
pub(crate) fn stack_summary_value(
    observation: &StackObservation,
    bytes: &LayerStackBytes,
) -> Value {
    json!({
        "layer_count": observation.layers.len(),
        "layers_bytes": bytes.total_bytes,
        "active_leases": observation.active_lease_count,
    })
}

/// Per-session layerstack view (`--workspace`): the layers `target` mounts
/// (base → newest) and, for each, the other sessions that also mount it, plus
/// the session's private upper bytes. Returns `None` when `target` is unknown.
pub(crate) fn workspace_layerstack_value(
    workspaces: &[RuntimeWorkspaceSnapshot],
    target: &str,
    upper_bytes: Option<u64>,
) -> Option<Value> {
    let session = workspaces
        .iter()
        .find(|workspace| workspace.workspace_id.0 == target)?;
    let mounts = session
        .layer_ids
        .iter()
        .map(|layer_id| {
            let shared_with = workspaces
                .iter()
                .filter(|other| {
                    other.workspace_id.0 != target
                        && other.layer_ids.iter().any(|id| id == layer_id)
                })
                .map(|other| other.workspace_id.0.clone())
                .collect::<Vec<_>>();
            json!({ "layer_id": layer_id, "shared_with": shared_with })
        })
        .collect::<Vec<_>>();
    Some(json!({
        "view": "layerstack",
        "workspace": target,
        "mounts": mounts,
        "upper_bytes": upper_bytes,
    }))
}
