//! File-only config loading: `defaults < prd.yml < local override`. No env, no
//! CLI selection — config is chosen by *file*, mirroring the sandbox config
//! model. The committed `agent-core/config/prd.yml` is the baseline; a gitignored
//! `agent-core/config/local.yml` (or, in tests, an explicit override file) is
//! merged over it (objects recurse, scalars/arrays replace), then the result is
//! deserialized into [`CentralConfig`] — where `deny_unknown_fields`, the
//! [`DatabaseUrl`] parse, and scalar coercion take effect — and validated.
//!
//! [`DatabaseUrl`]: crate::DatabaseUrl

use std::path::{Path, PathBuf};

use serde_yaml::Value;

use crate::config::CentralConfig;
use crate::error::ConfigError;
use crate::validation;

/// Load [`CentralConfig`] from the committed baseline `agent-core/config/prd.yml`
/// merged with the gitignored `agent-core/config/local.yml` override when present.
/// Missing files are skipped, so an absent baseline yields the defaults.
///
/// # Errors
/// Returns [`ConfigError`] on an unreadable/invalid YAML file, an unknown key, a
/// rejected database url, or a failed range check.
pub fn load() -> Result<CentralConfig, ConfigError> {
    load_layers(&[baseline_path(), local_override_path()])
}

/// Load the baseline merged with an explicit override file (the test/local seam).
///
/// # Errors
/// See [`load`].
pub fn load_with_override(override_path: impl AsRef<Path>) -> Result<CentralConfig, ConfigError> {
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

/// Fold `defaults < paths[0] < paths[1] < ...`, skipping files that do not exist.
fn load_layers(paths: &[PathBuf]) -> Result<CentralConfig, ConfigError> {
    let mut merged =
        serde_yaml::to_value(CentralConfig::default()).expect("serialize default config");
    for path in paths {
        if let Some(doc) = read_yaml(path)? {
            deep_merge(&mut merged, doc);
        }
    }
    let cfg: CentralConfig = serde_yaml::from_value(merged).map_err(ConfigError::ParseYaml)?;
    validation::validate(&cfg)?;
    Ok(cfg)
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

    /// Write a uniquely-named temp YAML file (no external tempfile dep).
    fn temp_yaml(name: &str, content: &str) -> PathBuf {
        let path = std::env::temp_dir().join(name);
        std::fs::write(&path, content).unwrap();
        path
    }

    // AC: override values win over the baseline, baseline-only fields survive,
    // and an untouched field keeps its default.
    #[test]
    fn test_override_wins_over_baseline_over_default() {
        let baseline = temp_yaml(
            "eos_config_baseline.yaml",
            "database:\n  pool_size: 10\n  busy_timeout_ms: 20\n",
        );
        let over = temp_yaml("eos_config_override.yaml", "database:\n  pool_size: 40\n");

        let cfg = load_layers(&[baseline.clone(), over.clone()]).unwrap();

        assert_eq!(cfg.database.pool_size, 40, "override wins");
        assert_eq!(
            cfg.database.busy_timeout_ms, 20,
            "baseline-only field survives"
        );
        assert!(cfg.database.wal, "untouched field keeps its default");

        let _ = std::fs::remove_file(&baseline);
        let _ = std::fs::remove_file(&over);
    }

    // AC: a missing layer file is skipped; absent files yield the defaults.
    #[test]
    fn test_missing_files_yield_defaults() {
        let absent = std::env::temp_dir().join("eos_config_intentionally_absent.yaml");
        let _ = std::fs::remove_file(&absent);
        assert_eq!(load_layers(&[absent]).unwrap(), CentralConfig::default());
    }

    // AC: an unknown key fails deserialization (deny_unknown_fields).
    #[test]
    fn test_unknown_key_rejected() {
        let bad = temp_yaml("eos_config_unknown_key.yaml", "database:\n  bogus: true\n");
        let result = load_layers(std::slice::from_ref(&bad));
        let _ = std::fs::remove_file(&bad);
        assert!(matches!(result, Err(ConfigError::ParseYaml(_))));
    }
}
