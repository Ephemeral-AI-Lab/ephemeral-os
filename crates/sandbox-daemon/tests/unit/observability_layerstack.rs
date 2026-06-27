use sandbox_observability::{LayerBytes, LayerStackBytes};
use sandbox_runtime::{LayerStatus, StackObservation};
use sandbox_runtime_layerstack::LayerRef;
use serde_json::json;

use crate::observability::layerstack::layerstack_view_value;

fn layer(id: &str, leased_by_workspaces: usize) -> LayerStatus {
    LayerStatus {
        layer: LayerRef {
            layer_id: id.to_owned(),
            path: format!("layers/{id}"),
        },
        leased_by_workspaces,
    }
}

fn bytes(id: &str, bytes: u64) -> LayerBytes {
    LayerBytes {
        layer_id: id.to_owned(),
        bytes,
    }
}

#[test]
fn layerstack_view_merges_bytes_and_derives_booked_by() {
    // §6 fixture: leases on {l2, l3} over l0..l4 (base → newest).
    let observation = StackObservation {
        manifest_version: 5,
        root_hash: "root-5".to_owned(),
        active_lease_count: 2,
        layers: vec![
            layer("l0", 0),
            layer("l1", 0),
            layer("l2", 1),
            layer("l3", 1),
            layer("l4", 0),
        ],
    };
    let disk = LayerStackBytes {
        layers: vec![
            bytes("l0", 120_000),
            bytes("l1", 84_000),
            bytes("l2", 20_000),
            bytes("l3", 20_000),
            bytes("l4", 5_000),
        ],
        total_bytes: 249_000,
    };

    let view = layerstack_view_value(&observation, &disk);

    assert_eq!(view["view"], json!("layerstack"));
    assert_eq!(view["manifest_version"], json!(5));
    assert_eq!(view["active_lease_count"], json!(2));
    assert_eq!(view["total_bytes"], json!(249_000));

    let layers = view["layers"].as_array().expect("layers array");
    assert_eq!(layers.len(), 5);

    // Bytes join by id.
    assert_eq!(layers[0]["bytes"], json!(120_000));
    assert_eq!(layers[2]["bytes"], json!(20_000));

    // leased by workspaces: only l2 and l3.
    assert_eq!(layers[2]["leased_by_workspaces"], json!(1));
    assert_eq!(layers[3]["leased_by_workspaces"], json!(1));
    assert_eq!(layers[0]["leased_by_workspaces"], json!(0));

    // booked by leased layers above (the §1 rule).
    assert_eq!(layers[0]["booked_by"], json!(["l2", "l3"]));
    assert_eq!(layers[1]["booked_by"], json!(["l2", "l3"]));
    assert_eq!(layers[2]["booked_by"], json!(["l3"]));
    assert_eq!(layers[3]["booked_by"], json!([]));
    assert_eq!(layers[4]["booked_by"], json!([]));
}

#[test]
fn layerstack_view_defaults_missing_layer_bytes_to_zero() {
    let observation = StackObservation {
        manifest_version: 1,
        root_hash: "root-1".to_owned(),
        active_lease_count: 0,
        layers: vec![layer("only", 0)],
    };
    let disk = LayerStackBytes::default();

    let view = layerstack_view_value(&observation, &disk);

    assert_eq!(view["layers"][0]["bytes"], json!(0));
    assert_eq!(view["total_bytes"], json!(0));
}
