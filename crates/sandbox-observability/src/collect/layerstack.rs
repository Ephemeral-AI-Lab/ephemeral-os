//! Pure on-disk reader for layerstack byte sizes.
//!
//! This is a leaf collector: it depends on `std` + `serde_json` only and never
//! imports `sandbox-runtime-layerstack`. It duplicates the minimal manifest
//! shape it needs rather than crossing that crate boundary, and it never panics
//! on malformed input — a half-written manifest yields an empty result.

use std::fs;
use std::path::{Path, PathBuf};

const ACTIVE_MANIFEST_FILE: &str = "manifest.json";
const LAYERS_DIR: &str = "layers";
const LAYER_METADATA_DIR: &str = ".layer-metadata";

const MAX_LAYER_WALK_NODES: usize = 1024;
const MAX_LAYER_WALK_DEPTH: usize = 64;

/// Disk byte size of a single layer, keyed by its id.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerBytes {
    pub layer_id: String,
    pub bytes: u64,
}

/// Per-layer disk byte sizes of the active manifest, plus their sum.
///
/// Empty when the manifest is missing or half-written. The byte facts here are
/// merged by the daemon with the in-memory lease state from
/// `LayerStack::observe()`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LayerStackBytes {
    pub layers: Vec<LayerBytes>,
    pub total_bytes: u64,
}

/// Read the byte size of every layer in the active manifest under `storage_root`.
///
/// Each layer is sized from its `.layer-metadata/<id>.bytes` sidecar; a missing
/// sidecar triggers a budgeted directory walk of `layers/<id>` that repopulates
/// the sidecar so the size is computed at most once per layer.
#[must_use]
pub fn sample_layerstack(storage_root: &Path) -> LayerStackBytes {
    let Some(layer_ids) = read_manifest_layer_ids(storage_root) else {
        return LayerStackBytes::default();
    };
    let mut layers = Vec::with_capacity(layer_ids.len());
    let mut total_bytes = 0_u64;
    for layer_id in layer_ids {
        let bytes = layer_bytes(storage_root, &layer_id);
        total_bytes = total_bytes.saturating_add(bytes);
        layers.push(LayerBytes { layer_id, bytes });
    }
    LayerStackBytes {
        layers,
        total_bytes,
    }
}

fn read_manifest_layer_ids(storage_root: &Path) -> Option<Vec<String>> {
    let raw = fs::read_to_string(storage_root.join(ACTIVE_MANIFEST_FILE)).ok()?;
    let document: serde_json::Value = serde_json::from_str(&raw).ok()?;
    let entries = document.get("layers")?.as_array()?;
    let mut ids = Vec::with_capacity(entries.len());
    for entry in entries {
        ids.push(entry.get("layer_id")?.as_str()?.to_owned());
    }
    Some(ids)
}

fn layer_bytes(storage_root: &Path, layer_id: &str) -> u64 {
    if let Some(bytes) = read_bytes_sidecar(storage_root, layer_id) {
        return bytes;
    }
    let bytes = walk_layer_bytes(&storage_root.join(LAYERS_DIR).join(layer_id));
    let _ = write_bytes_sidecar(storage_root, layer_id, bytes);
    bytes
}

fn bytes_sidecar_path(storage_root: &Path, layer_id: &str) -> PathBuf {
    storage_root
        .join(LAYER_METADATA_DIR)
        .join(format!("{layer_id}.bytes"))
}

fn read_bytes_sidecar(storage_root: &Path, layer_id: &str) -> Option<u64> {
    fs::read_to_string(bytes_sidecar_path(storage_root, layer_id))
        .ok()?
        .trim()
        .parse::<u64>()
        .ok()
}

fn write_bytes_sidecar(storage_root: &Path, layer_id: &str, bytes: u64) -> std::io::Result<()> {
    let dir = storage_root.join(LAYER_METADATA_DIR);
    fs::create_dir_all(&dir)?;
    fs::write(dir.join(format!("{layer_id}.bytes")), bytes.to_string())
}

fn walk_layer_bytes(root: &Path) -> u64 {
    let mut total = 0_u64;
    let mut stack = vec![(root.to_path_buf(), 0_usize)];
    let mut visited = 0_usize;
    while let Some((current, depth)) = stack.pop() {
        if visited >= MAX_LAYER_WALK_NODES {
            break;
        }
        visited += 1;
        let Ok(metadata) = fs::symlink_metadata(&current) else {
            continue;
        };
        let file_type = metadata.file_type();
        if file_type.is_file() {
            total = total.saturating_add(metadata.len());
        } else if file_type.is_dir() && depth < MAX_LAYER_WALK_DEPTH {
            let Ok(entries) = fs::read_dir(&current) else {
                continue;
            };
            for entry in entries.flatten() {
                if visited.saturating_add(stack.len()) >= MAX_LAYER_WALK_NODES {
                    break;
                }
                stack.push((entry.path(), depth.saturating_add(1)));
            }
        }
    }
    total
}
