//! Content-addressed store byte-identity (AV-1c) — the crown-jewel correctness
//! surface.
//!
//! Invariant: [`manifest_root_hash`] and [`layer_digest`] must reproduce the
//! live Rust hashes BYTE-FOR-BYTE. A single wrong byte is a silent data
//! divergence that passes every ASCII test. The two hashes are deliberately
//! OPPOSITE on non-ASCII handling:
//!
//! - `manifest_root_hash` serializes with ASCII-only JSON string escaping: every
//!   non-ASCII scalar is `\uXXXX`-escaped (hand-built here, since `serde_json`
//!   emits raw UTF-8 and would diverge).
//! - `layer_digest` hashes RAW UTF-8 path/source bytes with NUL framing.
//!

use std::collections::BTreeMap;
use std::fmt;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

/// On-disk layer-stack manifest schema version. Stamped into every persisted
/// manifest; a reader that does not understand it must refuse the load. NOT
/// part of the `manifest_root_hash` payload, so bumping it does not invalidate
/// layer hashes (see `docs/contract/02-cas-byte-identity.md`).
pub const MANIFEST_SCHEMA_VERSION: i64 = 1;

const LOWER_HEX: &[u8; 16] = b"0123456789abcdef";

/// Errors raised while parsing CAS path / manifest values.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[non_exhaustive]
pub enum CasError {
    /// A layer path was absolute, escaped the stack, was empty, or held a NUL.
    #[error("invalid layer path: {0}")]
    InvalidPath(String),
    /// A manifest carried an unsupported `schema_version`.
    #[error("unsupported manifest schema_version: {0}")]
    UnsupportedSchemaVersion(i64),
}

/// A normalized, relative, NUL-free layer path (`api-parse-dont-validate`).
///
/// Construct via [`LayerPath::parse`]; an invalid path is unrepresentable.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct LayerPath(String);

impl LayerPath {
    /// Normalize a raw path string exactly as Rust `normalize_layer_path`:
    /// `\` -> `/`, strip surrounding whitespace, drop empty / `.` segments,
    /// reject absolute / `..` / NUL / empty-result.
    ///
    /// # Errors
    ///
    /// Returns [`CasError::InvalidPath`] when the normalized path would be
    /// empty, absolute, escaping, or contain a NUL byte.
    pub fn parse(path: &str) -> Result<Self, CasError> {
        let raw = path.replace('\\', "/");
        let raw = raw.trim();
        if raw.contains('\0') {
            return Err(CasError::InvalidPath(path.to_owned()));
        }
        // PurePosixPath: a leading '/' makes the path absolute.
        if raw.starts_with('/') {
            return Err(CasError::InvalidPath(path.to_owned()));
        }
        let mut parts: Vec<&str> = Vec::new();
        for part in raw.split('/') {
            if part.is_empty() || part == "." {
                continue;
            }
            if part == ".." {
                return Err(CasError::InvalidPath(path.to_owned()));
            }
            parts.push(part);
        }
        if parts.is_empty() {
            return Err(CasError::InvalidPath(path.to_owned()));
        }
        Ok(Self(parts.join("/")))
    }

    /// The normalized path string.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for LayerPath {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// One layer reference in a manifest: `{layer_id, path}` (both strings).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LayerRef {
    pub layer_id: String,
    pub path: String,
}

/// The persisted manifest. `version`/`schema_version` are NOT hashed by
/// [`manifest_root_hash`]; only `layers` (in given order) is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Manifest {
    pub version: i64,
    pub layers: Vec<LayerRef>,
    pub schema_version: i64,
}

impl Manifest {
    /// Construct a manifest, rejecting any `schema_version` that does not equal
    /// [`MANIFEST_SCHEMA_VERSION`].
    ///
    /// # Errors
    ///
    /// Returns [`CasError::UnsupportedSchemaVersion`] when `schema_version`
    /// does not match [`MANIFEST_SCHEMA_VERSION`].
    pub fn new(version: i64, layers: Vec<LayerRef>, schema_version: i64) -> Result<Self, CasError> {
        if schema_version != MANIFEST_SCHEMA_VERSION {
            return Err(CasError::UnsupportedSchemaVersion(schema_version));
        }
        Ok(Self {
            version,
            layers,
            schema_version,
        })
    }

    /// Number of layers (the manifest-depth invariant surface).
    #[must_use]
    pub fn depth(&self) -> usize {
        self.layers.len()
    }
}

/// Append the ASCII-only JSON string escaping of `s` (without surrounding
/// quotes) to `out`: control/quote/backslash use short escapes and every
/// non-ASCII scalar becomes `\uXXXX` (surrogate pairs for non-BMP).
///
fn push_json_ascii_escaped(out: &mut String, s: &str) {
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{0008}' => out.push_str("\\b"),
            '\u{0009}' => out.push_str("\\t"),
            '\u{000A}' => out.push_str("\\n"),
            '\u{000C}' => out.push_str("\\f"),
            '\u{000D}' => out.push_str("\\r"),
            c if (0x20..=0x7E).contains(&u32::from(c)) => out.push(c),
            c if u32::from(c) < 0x20 => {
                // other control chars: lowercase 4-digit \u00XX
                push_u_escape(out, u32::from(c));
            }
            c => {
                let cp = u32::from(c);
                if cp <= 0xFFFF {
                    push_u_escape(out, cp);
                } else {
                    // UTF-16 surrogate pair, both lowercase.
                    let v = cp - 0x10000;
                    let hi = 0xD800 + (v >> 10);
                    let lo = 0xDC00 + (v & 0x3FF);
                    push_u_escape(out, hi);
                    push_u_escape(out, lo);
                }
            }
        }
    }
}

fn push_u_escape(out: &mut String, value: u32) {
    out.push_str("\\u");
    out.push(hex_char((value >> 12) & 0x0f));
    out.push(hex_char((value >> 8) & 0x0f));
    out.push(hex_char((value >> 4) & 0x0f));
    out.push(hex_char(value & 0x0f));
}

/// Build the exact `json.dumps({"layers":[...]}, sort_keys=True,
/// separators=(",",":"))` byte string the manifest root hash is computed over.
fn manifest_layers_json(layers: &[LayerRef]) -> String {
    let mut out = String::from("{\"layers\":[");
    for (i, layer) in layers.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        // sort_keys: "layer_id" < "path" (code-point order), so layer_id first.
        out.push_str("{\"layer_id\":\"");
        push_json_ascii_escaped(&mut out, &layer.layer_id);
        out.push_str("\",\"path\":\"");
        push_json_ascii_escaped(&mut out, &layer.path);
        out.push_str("\"}");
    }
    out.push_str("]}");
    out
}

/// Stable identity hash for a manifest's root view.
///
/// Hashes ONLY `{"layers":...}` in GIVEN order (order-sensitive);
/// `ensure_ascii=True` escaping applied.
#[must_use]
pub fn manifest_root_hash(manifest: &Manifest) -> String {
    let encoded = manifest_layers_json(&manifest.layers);
    let mut hasher = Sha256::new();
    hasher.update(encoded.as_bytes());
    hex_lower(&hasher.finalize())
}

/// A storage-level layer change.
///
/// Tagged union by kind. `path` is the post-normalization form; `Write` carries
/// raw bytes hashed verbatim.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LayerChange {
    /// File write; `content` is hashed RAW (may be empty / contain NUL / binary).
    Write { path: LayerPath, content: Vec<u8> },
    /// File/dir removal (whiteout). No payload hashed.
    Delete { path: LayerPath },
    /// Symlink; `source_path` (the link target) is hashed RAW UTF-8.
    Symlink {
        path: LayerPath,
        source_path: String,
    },
    /// Opaque-directory marker. No payload hashed.
    OpaqueDir { path: LayerPath },
}

impl LayerChange {
    /// The `kind` discriminator string fed to the digest.
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::Write { .. } => "write",
            Self::Delete { .. } => "delete",
            Self::Symlink { .. } => "symlink",
            Self::OpaqueDir { .. } => "opaque_dir",
        }
    }

    /// The normalized path this change targets.
    #[must_use]
    pub const fn path(&self) -> &LayerPath {
        match self {
            Self::Write { path, .. }
            | Self::Delete { path }
            | Self::Symlink { path, .. }
            | Self::OpaqueDir { path } => path,
        }
    }
}

/// Last-write-wins per `path`, then emit in ascending `path` order.
/// Input-order-insensitive (the OPPOSITE of `manifest_root_hash`).
#[must_use]
pub fn aggregate_layer_changes(changes: &[LayerChange]) -> Vec<LayerChange> {
    // BTreeMap gives sorted-by-path emission; insertion overwrites (last-write-wins).
    let mut by_path: BTreeMap<LayerPath, LayerChange> = BTreeMap::new();
    for change in changes.iter().cloned() {
        by_path.insert(change.path().clone(), change);
    }
    by_path.into_values().collect()
}

/// Feed one change's framed bytes into the running digest:
/// `kind ‖ \0 ‖ path ‖ \0 ‖ <payload-or-nothing> ‖ \0`. Trailing `\0` always.
fn update_digest(hasher: &mut Sha256, change: &LayerChange) {
    hasher.update(change.kind().as_bytes());
    hasher.update(b"\0");
    hasher.update(change.path().as_str().as_bytes());
    hasher.update(b"\0");
    match change {
        LayerChange::Write { content, .. } => hasher.update(content),
        LayerChange::Symlink { source_path, .. } => hasher.update(source_path.as_bytes()),
        LayerChange::Delete { .. } | LayerChange::OpaqueDir { .. } => {}
    }
    hasher.update(b"\0");
}

/// Per-layer change-set digest: sha256 over `aggregate_layer_changes(changes)`.
#[must_use]
pub fn layer_digest(changes: &[LayerChange]) -> String {
    let mut hasher = Sha256::new();
    for change in aggregate_layer_changes(changes) {
        update_digest(&mut hasher, &change);
    }
    hex_lower(&hasher.finalize())
}

/// Lowercase hex of a digest, matching Rust `hexdigest()`.
fn hex_lower(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(char::from(LOWER_HEX[usize::from(b >> 4)]));
        s.push(char::from(LOWER_HEX[usize::from(b & 0x0f)]));
    }
    s
}

fn hex_char(nibble: u32) -> char {
    let index = usize::from((nibble & 0x0f) as u8);
    char::from(LOWER_HEX[index])
}

#[cfg(test)]
#[path = "../tests/unit/model.rs"]
mod tests;
