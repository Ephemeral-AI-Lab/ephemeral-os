//! Agent-loop runtime configuration owned by `eos-engine`.

use std::path::{Path, PathBuf};
use std::time::Duration;

use eos_types::ConfigError;
use serde::{Deserialize, Serialize};
use serde_yaml::Value;

/// Default poll interval for background completion monitors.
pub const DEFAULT_BACKGROUND_COMPLETION_POLL_INTERVAL_MS: u64 = 1000;

/// Runtime tunables for engine-owned loop behavior.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct EngineRuntimeConfig {
    /// Poll interval shared by command, workflow, and subagent completion monitors.
    #[serde(default = "default_background_completion_poll_interval_ms")]
    pub background_completion_poll_interval_ms: u64,
}

impl Default for EngineRuntimeConfig {
    fn default() -> Self {
        Self {
            background_completion_poll_interval_ms: DEFAULT_BACKGROUND_COMPLETION_POLL_INTERVAL_MS,
        }
    }
}

impl EngineRuntimeConfig {
    /// Load the `runtime` section from `config_dir/prd.yml < config_dir/local.yml`.
    ///
    /// Missing files are skipped; the merged document must contain a `runtime`
    /// section matching [`EngineRuntimeConfig`].
    ///
    /// # Errors
    /// Returns [`ConfigError`] when a present file cannot be read or parsed, the
    /// merged document has no `runtime` section, or validation fails.
    pub fn load_from_dir(config_dir: impl AsRef<Path>) -> Result<Self, ConfigError> {
        let config_dir = config_dir.as_ref();
        Self::load_from_paths(&[config_dir.join("prd.yml"), config_dir.join("local.yml")])
    }

    /// Load the `runtime` section from an explicit ordered list of YAML files.
    ///
    /// Files are merged left to right: mappings recurse, while scalars and
    /// arrays replace the previous value.
    ///
    /// # Errors
    /// Returns [`ConfigError`] when a present file cannot be read or parsed, the
    /// merged document has no `runtime` section, or validation fails.
    pub fn load_from_paths(paths: &[PathBuf]) -> Result<Self, ConfigError> {
        let mut merged = Value::Mapping(serde_yaml::Mapping::new());
        for path in paths {
            if let Some(doc) = read_yaml(path)? {
                deep_merge(&mut merged, doc);
            }
        }

        let mapping = merged
            .as_mapping()
            .ok_or(ConfigError::InvalidDocumentRoot)?;
        let runtime = mapping
            .get(Value::String("runtime".to_owned()))
            .ok_or_else(|| ConfigError::MissingSection {
                section: "runtime".to_owned(),
            })?;
        let config: Self =
            serde_yaml::from_value(runtime.clone()).map_err(ConfigError::ParseYaml)?;
        config.validate()?;
        Ok(config)
    }

    /// Return the background completion poll interval as a [`Duration`].
    #[must_use]
    pub fn background_completion_poll_interval(&self) -> Duration {
        Duration::from_millis(self.background_completion_poll_interval_ms)
    }

    /// Enforce numeric range constraints.
    ///
    /// # Errors
    /// Returns [`ConfigError::OutOfRange`] when the background completion poll
    /// interval is zero.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.background_completion_poll_interval_ms < 1 {
            return Err(ConfigError::OutOfRange {
                field: "runtime.background_completion_poll_interval_ms".to_owned(),
                detail: "must be >= 1".to_owned(),
            });
        }
        Ok(())
    }
}

const fn default_background_completion_poll_interval_ms() -> u64 {
    DEFAULT_BACKGROUND_COMPLETION_POLL_INTERVAL_MS
}

fn read_yaml(path: &Path) -> Result<Option<Value>, ConfigError> {
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(path).map_err(ConfigError::ReadFile)?;
    let doc: Value = serde_yaml::from_str(&text).map_err(ConfigError::ParseYaml)?;
    Ok((!doc.is_null()).then_some(doc))
}

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
#[path = "../../tests/agent_loop/config/mod.rs"]
mod tests;
