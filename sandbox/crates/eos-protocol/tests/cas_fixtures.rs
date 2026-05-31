//! Golden CAS byte-identity fixtures (AV-1c). ALL 18 cases must pass; the
//! unicode cases prove the `ensure_ascii=True` escaper. Fixtures are immutable
//! ground truth produced by the live Python — never edit them to match code.

use base64::Engine as _;
use eos_protocol::cas::{
    layer_digest, manifest_root_hash, LayerChange, LayerPath, LayerRef, Manifest,
};
use serde_json::Value;

const CASES: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/fixtures/cas/cases.json"
));

fn build_layer_change(c: &Value) -> LayerChange {
    let kind = c["kind"].as_str().expect("kind string");
    let path = LayerPath::parse(c["path"].as_str().expect("path string")).expect("valid path");
    match kind {
        "write" => {
            let b64 = c["write_content_b64"].as_str().expect("write_content_b64");
            let content = base64::engine::general_purpose::STANDARD
                .decode(b64)
                .expect("valid base64");
            LayerChange::Write { path, content }
        }
        "delete" => LayerChange::Delete { path },
        "symlink" => LayerChange::Symlink {
            path,
            source_path: c["source_path"].as_str().expect("source_path").to_owned(),
        },
        "opaque_dir" => LayerChange::OpaqueDir { path },
        other => panic!("unknown change kind: {other}"),
    }
}

#[test]
fn all_cas_fixtures_match() {
    let cases: Vec<Value> = serde_json::from_str(CASES).expect("parse cases.json");
    assert_eq!(cases.len(), 18, "expected 18 golden cases");

    let mut checked = 0usize;
    for case in &cases {
        let name = case["name"].as_str().expect("name");
        let kind = case["kind"].as_str().expect("kind");
        let expected = case["expected"].as_str().expect("expected");

        let actual = match kind {
            "manifest_root_hash" => {
                let layers: Vec<LayerRef> = case["input"]["layers"]
                    .as_array()
                    .expect("layers array")
                    .iter()
                    .map(|l| LayerRef {
                        layer_id: l["layer_id"].as_str().expect("layer_id").to_owned(),
                        path: l["path"].as_str().expect("path").to_owned(),
                    })
                    .collect();
                let manifest = Manifest::new(layers.len() as i64, layers, 1).expect("manifest");
                manifest_root_hash(&manifest)
            }
            "layer_digest" => {
                let changes: Vec<LayerChange> = case["input"]["changes"]
                    .as_array()
                    .expect("changes array")
                    .iter()
                    .map(build_layer_change)
                    .collect();
                // Cross-check the documented aggregate ordering too.
                if let Some(order) = case["aggregated_order"].as_array() {
                    let agg = eos_protocol::cas::aggregate_layer_changes(&changes);
                    let agg_paths: Vec<&str> = agg.iter().map(|c| c.path().as_str()).collect();
                    let expected_order: Vec<&str> =
                        order.iter().map(|v| v.as_str().expect("path")).collect();
                    assert_eq!(agg_paths, expected_order, "aggregate order for {name}");
                }
                layer_digest(&changes)
            }
            other => panic!("unknown case kind: {other}"),
        };

        assert_eq!(actual, expected, "hash mismatch for case {name}");
        checked += 1;
    }
    assert_eq!(checked, 18);
}
