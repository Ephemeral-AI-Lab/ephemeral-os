use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use sandbox_observability_telemetry::{sample_layerstack, LayerBytes, LayerStackBytes, WalkBudget};

type TestResult = Result<(), Box<dyn Error>>;

static NEXT: AtomicU64 = AtomicU64::new(0);

struct TempStorage {
    root: PathBuf,
}

impl TempStorage {
    fn new(label: &str) -> std::io::Result<Self> {
        let root = std::env::temp_dir().join(format!(
            "sandbox-obs-layerstack-{label}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root)?;
        Ok(Self { root })
    }

    fn root(&self) -> &Path {
        &self.root
    }

    fn write_manifest(&self, layer_ids: &[&str]) -> std::io::Result<()> {
        let entries: Vec<String> = layer_ids
            .iter()
            .map(|id| format!("{{\"layer_id\":\"{id}\",\"path\":\"layers/{id}\"}}"))
            .collect();
        let body = format!(
            "{{\"schema_version\":1,\"version\":1,\"layers\":[{}]}}",
            entries.join(",")
        );
        fs::write(self.root.join("manifest.json"), body)
    }

    fn write_layer_file(&self, layer_id: &str, name: &str, bytes: &[u8]) -> std::io::Result<()> {
        let dir = self.root.join("layers").join(layer_id);
        fs::create_dir_all(&dir)?;
        fs::write(dir.join(name), bytes)
    }

    fn write_sidecar(&self, layer_id: &str, bytes: u64) -> std::io::Result<()> {
        let dir = self.root.join(".layer-metadata");
        fs::create_dir_all(&dir)?;
        fs::write(dir.join(format!("{layer_id}.bytes")), bytes.to_string())
    }

    fn sidecar(&self, layer_id: &str) -> Option<String> {
        fs::read_to_string(
            self.root
                .join(".layer-metadata")
                .join(format!("{layer_id}.bytes")),
        )
        .ok()
    }
}

impl Drop for TempStorage {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn sidecar_size_is_used_without_walking() -> TestResult {
    let storage = TempStorage::new("sidecar-only")?;
    storage.write_manifest(&["L1"])?;
    // Sidecar present but the layer directory is absent: a walk would yield 0,
    // so a 5000-byte result proves the sidecar was used.
    storage.write_sidecar("L1", 5000)?;

    let observed = sample_layerstack(storage.root(), WalkBudget::default());

    assert_eq!(
        observed,
        LayerStackBytes {
            layers: vec![LayerBytes {
                layer_id: "L1".to_owned(),
                bytes: 5000,
            }],
            total_bytes: 5000,
        }
    );
    Ok(())
}

#[test]
fn missing_sidecar_walks_and_repopulates() -> TestResult {
    let storage = TempStorage::new("walk-fallback")?;
    storage.write_manifest(&["L2"])?;
    storage.write_layer_file("L2", "a.txt", &[0_u8; 10])?;
    assert!(
        storage.sidecar("L2").is_none(),
        "sidecar absent before sample"
    );

    let observed = sample_layerstack(storage.root(), WalkBudget::default());

    assert_eq!(observed.total_bytes, 10);
    assert_eq!(
        observed.layers,
        vec![LayerBytes {
            layer_id: "L2".to_owned(),
            bytes: 10
        }]
    );
    assert_eq!(
        storage.sidecar("L2").as_deref(),
        Some("10"),
        "walk repopulates the sidecar"
    );
    Ok(())
}

#[test]
fn cached_sidecar_keeps_layer_sized_once() -> TestResult {
    let storage = TempStorage::new("sized-once")?;
    storage.write_manifest(&["L3"])?;
    storage.write_layer_file("L3", "a.txt", &[0_u8; 10])?;

    let first = sample_layerstack(storage.root(), WalkBudget::default());
    assert_eq!(first.total_bytes, 10);

    // Grow the layer on disk; the cached sidecar must still win, so the layer is
    // sized exactly once.
    storage.write_layer_file("L3", "b.txt", &[0_u8; 90])?;
    let second = sample_layerstack(storage.root(), WalkBudget::default());
    assert_eq!(second.total_bytes, 10);
    Ok(())
}

#[test]
fn half_written_manifest_is_skipped_without_panic() -> TestResult {
    let storage = TempStorage::new("half-written")?;
    fs::write(storage.root().join("manifest.json"), "{\"layers\":[ {\"lay")?;

    let observed = sample_layerstack(storage.root(), WalkBudget::default());

    assert_eq!(observed, LayerStackBytes::default());
    Ok(())
}

#[test]
fn missing_manifest_is_empty() -> TestResult {
    let storage = TempStorage::new("missing-manifest")?;

    let observed = sample_layerstack(storage.root(), WalkBudget::default());

    assert_eq!(observed, LayerStackBytes::default());
    Ok(())
}
