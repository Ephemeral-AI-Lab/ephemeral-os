//! File-only config loading: `prd.yml < local override`. No env, no CLI
//! selection — config is chosen by *file*, mirroring the sandbox config model.
//! The committed `agent-core/config/prd.yml` is the baseline; a gitignored
//! `agent-core/config/local.yml` (or, in tests, an explicit override file) is
//! merged over it (objects recurse, scalars/arrays replace). The result is a
//! [`ConfigDocument`]; each crate deserializes its section via
//! [`ConfigDocument::section`].

use std::path::{Path, PathBuf};

use serde_yaml::Value;

use crate::document::ConfigDocument;
use crate::error::ConfigError;

/// Load the merged config document from the committed baseline
/// `agent-core/config/prd.yml` overlaid by the gitignored
/// `agent-core/config/local.yml` when present. Missing files are skipped.
///
/// # Errors
/// Returns [`ConfigError`] on an unreadable or invalid YAML file.
pub fn load() -> Result<ConfigDocument, ConfigError> {
    load_layers(&[baseline_path(), local_override_path()])
}

/// Load the baseline merged with an explicit override file (the test/local seam).
///
/// # Errors
/// See [`load`].
pub fn load_with_override(override_path: impl AsRef<Path>) -> Result<ConfigDocument, ConfigError> {
    load_layers(&[baseline_path(), override_path.as_ref().to_path_buf()])
}

/// The committed baseline path `agent-core/config/prd.yml`.
fn baseline_path() -> PathBuf {
    config_dir().join("prd.yml")
}

/// The gitignored production override path `agent-core/config/local.yml`.
fn local_override_path() -> PathBuf {
    config_dir().join("local.yml")
}

/// `agent-core/config` resolved from the crate layout (this crate lives at
/// `agent-core/crates/eos-config`).
fn config_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map_or_else(|| PathBuf::from("config"), |root| root.join("config"))
}

/// Fold `paths[0] < paths[1] < ...`, skipping files that do not exist. An empty
/// or all-absent set yields an empty document (every `section()` then misses).
fn load_layers(paths: &[PathBuf]) -> Result<ConfigDocument, ConfigError> {
    let mut merged = Value::Mapping(serde_yaml::Mapping::new());
    for path in paths {
        if let Some(doc) = read_yaml(path)? {
            deep_merge(&mut merged, doc);
        }
    }
    Ok(ConfigDocument::from_value(merged))
}

/// Read and parse a YAML file, returning `None` when it is absent or empty.
fn read_yaml(path: &Path) -> Result<Option<Value>, ConfigError> {
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(path).map_err(ConfigError::ReadFile)?;
    let doc: Value = serde_yaml::from_str(&text).map_err(ConfigError::ParseYaml)?;
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

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use crate::DatabaseConfig;

    /// Write a uniquely-named temp YAML file (no external tempfile dep).
    fn temp_yaml(name: &str, content: &str) -> PathBuf {
        let path = std::env::temp_dir().join(name);
        std::fs::write(&path, content).unwrap();
        path
    }

    // A complete baseline section deserializes via `section()`; an override
    // merges over it at the value level (override field wins, baseline fields
    // survive) before deserialization.
    #[test]
    fn test_override_merges_then_section_deserializes() {
        let baseline = temp_yaml(
            "eos_config_baseline.yaml",
            "database:\n  url: \"sqlite:///./x.db\"\n  pool_size: 10\n  busy_timeout_ms: 20\n  wal: true\n  foreign_keys: true\n",
        );
        let over = temp_yaml("eos_config_override.yaml", "database:\n  pool_size: 40\n");

        let doc = load_layers(&[baseline.clone(), over.clone()]).unwrap();
        let db: DatabaseConfig = doc.section("database").unwrap();

        assert_eq!(db.pool_size, 40, "override wins");
        assert_eq!(db.busy_timeout_ms, 20, "baseline-only field survives");
        assert!(db.wal);

        let _ = std::fs::remove_file(&baseline);
        let _ = std::fs::remove_file(&over);
    }

    // A missing section is reported as MissingSection.
    #[test]
    fn test_missing_section_errors() {
        let absent = std::env::temp_dir().join("eos_config_intentionally_absent.yaml");
        let _ = std::fs::remove_file(&absent);
        let doc = load_layers(&[absent]).unwrap();
        let result: Result<DatabaseConfig, _> = doc.section("database");
        assert!(matches!(result, Err(ConfigError::MissingSection { .. })));
    }

    // An unknown field within a section fails deserialization (deny_unknown_fields).
    #[test]
    fn test_unknown_field_rejected() {
        let bad = temp_yaml(
            "eos_config_unknown_field.yaml",
            "database:\n  url: \"sqlite:///./x.db\"\n  pool_size: 5\n  busy_timeout_ms: 5000\n  wal: true\n  foreign_keys: true\n  bogus: 1\n",
        );
        let doc = load_layers(std::slice::from_ref(&bad)).unwrap();
        let result: Result<DatabaseConfig, _> = doc.section("database");
        let _ = std::fs::remove_file(&bad);
        assert!(matches!(result, Err(ConfigError::ParseYaml(_))));
    }
}
