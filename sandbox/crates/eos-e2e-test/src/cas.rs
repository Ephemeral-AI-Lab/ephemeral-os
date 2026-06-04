//! In-process CAS hash recompute — the byte-identity oracle.
//!
//! Re-exports the `eos-protocol` CAS primitives and adds thin builders so a test
//! can recompute `manifest_root_hash` / `layer_digest` from inputs it controls,
//! then compare against what the daemon reports.
//!
//! Note (protocol-only constraint): the live manifest's full `LayerRef` list is
//! NOT exposed by any op, so a *blind* recompute of the daemon's current
//! `manifest_root_hash` is not possible. These helpers are for (a) hashing
//! content the test itself authored and (b) asserting determinism / format of
//! daemon-reported digests.

use anyhow::Result;
use eos_protocol::MANIFEST_SCHEMA_VERSION;
pub use eos_protocol::{
    layer_digest, manifest_root_hash, LayerChange, LayerPath, LayerRef, Manifest,
};

/// Recompute `manifest_root_hash` for a known `(layer_id, path)` layer list.
///
/// # Errors
/// Returns an error if `Manifest::new` rejects the inputs.
pub fn manifest_hash(version: i64, layers: &[(&str, &str)]) -> Result<String> {
    let layers = layers
        .iter()
        .map(|(layer_id, path)| LayerRef {
            layer_id: (*layer_id).to_owned(),
            path: (*path).to_owned(),
        })
        .collect();
    let manifest = Manifest::new(version, layers, MANIFEST_SCHEMA_VERSION)?;
    Ok(manifest_root_hash(&manifest))
}

/// Recompute `layer_digest` for a set of file writes `(path, content)`.
///
/// # Errors
/// Returns an error if any path fails `LayerPath::parse`.
pub fn layer_digest_for_writes(writes: &[(&str, &[u8])]) -> Result<String> {
    let mut changes = Vec::with_capacity(writes.len());
    for (path, content) in writes {
        changes.push(LayerChange::Write {
            path: LayerPath::parse(path)?,
            content: content.to_vec(),
        });
    }
    Ok(layer_digest(&changes))
}

/// A daemon-reported hash looks like a 64-char lowercase hex SHA-256.
#[must_use]
pub fn looks_like_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}
