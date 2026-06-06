//! File-only config loading: `backend.yml < local.yml`. No env, no CLI
//! selection — config is chosen by file, mirroring the agent-core/sandbox model.
//! The committed `backend-server/config/backend.yml` is the baseline; a
//! gitignored `backend-server/config/local.yml` is merged over it (objects
//! recurse, scalars/arrays replace). The merged document deserializes to a
//! [`ServerConfig`], which is then range-validated.

use std::path::{Path, PathBuf};

use serde_yaml::Value;

use crate::server::ServerConfig;

/// Errors raised while loading backend config.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum ConfigError {
    /// A config file could not be read.
    #[error("failed to read config file {}", .path.display())]
    ReadFile {
        /// The file that could not be read.
        path: PathBuf,
        /// The underlying I/O failure.
        #[source]
        source: std::io::Error,
    },
    /// A config file was not valid YAML.
    #[error("failed to parse config file {}", .path.display())]
    ParseFile {
        /// The file that failed to parse.
        path: PathBuf,
        /// The underlying YAML parse failure.
        #[source]
        source: serde_yaml::Error,
    },
    /// The merged config document did not match the expected schema.
    #[error("merged config did not match the expected schema")]
    Schema(#[from] serde_yaml::Error),
    /// A numeric field was outside its accepted range.
    #[error("config field {field} out of range: {detail}")]
    OutOfRange {
        /// The offending field path.
        field: &'static str,
        /// Why it was rejected.
        detail: &'static str,
    },
    /// A required string/path field was empty.
    #[error("config field {field} must not be empty")]
    Empty {
        /// The offending field path.
        field: &'static str,
    },
}

/// Load `backend.yml` overlaid by `local.yml` (when present), then validate.
///
/// # Errors
/// Returns [`ConfigError`] on an unreadable/invalid file or an out-of-range field.
pub fn load() -> Result<ServerConfig, ConfigError> {
    load_from_paths(&[baseline_path(), local_override_path()])
}

/// Load and validate `ServerConfig` from an explicit layered list of files,
/// folding `paths[0] < paths[1] < ...` and skipping absent files. This is the
/// embedding/test seam; production uses [`load`].
///
/// # Errors
/// See [`load`].
pub fn load_from_paths(paths: &[PathBuf]) -> Result<ServerConfig, ConfigError> {
    let mut merged = Value::Mapping(serde_yaml::Mapping::new());
    for path in paths {
        if let Some(doc) = read_yaml(path)? {
            deep_merge(&mut merged, doc);
        }
    }
    let config: ServerConfig = serde_yaml::from_value(merged)?;
    config.validate()?;
    Ok(config)
}

/// The committed baseline path `backend-server/config/backend.yml`.
fn baseline_path() -> PathBuf {
    config_dir().join("backend.yml")
}

/// The gitignored override path `backend-server/config/local.yml`.
fn local_override_path() -> PathBuf {
    config_dir().join("local.yml")
}

/// `backend-server/config`, resolved from the crate layout (this crate lives at
/// `backend-server/crates/eos-backend-config`).
fn config_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map_or_else(|| PathBuf::from("config"), |root| root.join("config"))
}

/// Read and parse a YAML file, returning `None` when it is absent or empty.
fn read_yaml(path: &Path) -> Result<Option<Value>, ConfigError> {
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(path).map_err(|source| ConfigError::ReadFile {
        path: path.to_owned(),
        source,
    })?;
    let doc: Value = serde_yaml::from_str(&text).map_err(|source| ConfigError::ParseFile {
        path: path.to_owned(),
        source,
    })?;
    Ok((!doc.is_null()).then_some(doc))
}

/// Recursively merge `overlay` into `base`: two mappings merge key-by-key, any
/// other overlay node replaces the base node.
fn deep_merge(base: &mut Value, overlay: Value) {
    match (base, overlay) {
        (Value::Mapping(base_map), Value::Mapping(overlay_map)) => {
            for (key, value) in overlay_map {
                match base_map.get_mut(&key) {
                    Some(existing) => deep_merge(existing, value),
                    None => {
                        base_map.insert(key, value);
                    }
                }
            }
        }
        (slot, overlay) => *slot = overlay,
    }
}
