use crate::test_fixture::Fixture;
use crate::{LayerChange, LayerPath, LayerStack, LAYER_METADATA_DIR};

#[test]
fn publish_writes_layer_bytes_sidecar() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = Fixture::new("publish_bytes_sidecar")?;
    let mut stack = LayerStack::open(fixture.root.clone())?;

    stack.publish_layer(&[LayerChange::Write {
        path: LayerPath::parse("hello.txt")?,
        content: b"content".to_vec(),
    }])?;

    let manifest = stack.read_active_manifest()?;
    let Some(layer) = manifest.layers.first() else {
        return Err("published layer missing from manifest".into());
    };
    let sidecar = fixture
        .root
        .join(LAYER_METADATA_DIR)
        .join(format!("{}.bytes", layer.layer_id));

    // The reader (sandbox-observability collect/layerstack.rs) parses this exact
    // path as a decimal u64, so the contract is the path and the encoding.
    assert_eq!(std::fs::read_to_string(&sidecar)?, "7");
    Ok(())
}
