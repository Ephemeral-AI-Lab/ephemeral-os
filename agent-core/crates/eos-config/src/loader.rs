//! Layered config loading with precedence `defaults < YAML < env < init`
//! (`central.py`/`loader.py`). The merge is hand-rolled over [`serde_yaml::Value`]
//! trees (a `figment` dependency would be speculative for a recursive map-merge):
//! the defaults are serialized to a full tree, each higher-priority source is
//! deep-merged over it, the provider-key alias is applied, and the result is
//! deserialized into [`CentralConfig`] — which is where `deny_unknown_fields`,
//! the [`DatabaseUrl`] parse, and scalar coercion take effect — then validated.
//!
//! The Python `ContextVar` override / lazy global is intentionally not ported
//! (spec-conventions §7): tests pass an explicit [`ConfigLoader::env`]/`init`
//! instead of mutating process or global state.
//!
//! [`DatabaseUrl`]: crate::DatabaseUrl

use std::path::PathBuf;

use serde_yaml::Value;

use crate::config::CentralConfig;
use crate::env::{data_from_env, EnvMap};
use crate::error::ConfigError;
use crate::{paths, validation};

/// Builder for [`CentralConfig`] loading. Defaults read the process environment
/// and discover the central YAML; tests inject an explicit env / YAML path /
/// init layer for determinism.
#[derive(Debug)]
pub struct ConfigLoader {
    yaml_path: Option<PathBuf>,
    env: EnvMap,
    init: Value,
}

impl Default for ConfigLoader {
    fn default() -> Self {
        Self {
            yaml_path: None,
            env: std::env::vars().collect(),
            init: Value::Null,
        }
    }
}

impl ConfigLoader {
    /// A loader reading the process environment and discovering the central YAML.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Use an explicit YAML file instead of central-config discovery.
    #[must_use]
    pub fn yaml_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.yaml_path = Some(path.into());
        self
    }

    /// Use an explicit environment map instead of the process environment.
    #[must_use]
    pub fn env(mut self, env: EnvMap) -> Self {
        self.env = env;
        self
    }

    /// Set the highest-priority init-override layer (a partial mapping).
    #[must_use]
    pub fn init(mut self, init: Value) -> Self {
        self.init = init;
        self
    }

    /// Load, merge, alias-rename, deserialize, and validate the config.
    ///
    /// # Errors
    /// Returns [`ConfigError`] on an unreadable/invalid YAML file, an unknown
    /// key, an invalid value, a rejected database url, or a failed range /
    /// contradiction check.
    pub fn load(self) -> Result<CentralConfig, ConfigError> {
        // defaults (a full tree) < YAML < env < init (init highest). The
        // provider alias is applied per-source (matching `loader.py`, which
        // renames inside `_data_from_env`): normalizing after the merge would
        // see the always-present default `default_provider` and wrongly drop a
        // source-supplied `provider`.
        let mut merged =
            serde_yaml::to_value(CentralConfig::default()).expect("serialize default config");
        for mut source in [
            self.read_yaml()?,
            Some(data_from_env(&self.env)),
            self.init_layer(),
        ]
        .into_iter()
        .flatten()
        {
            apply_provider_alias(&mut source);
            deep_merge(&mut merged, source);
        }

        let cfg: CentralConfig = serde_yaml::from_value(merged).map_err(ConfigError::ParseYaml)?;
        validation::validate(&cfg)?;
        Ok(cfg)
    }

    fn init_layer(&self) -> Option<Value> {
        (!self.init.is_null()).then(|| self.init.clone())
    }

    fn read_yaml(&self) -> Result<Option<Value>, ConfigError> {
        let path = match &self.yaml_path {
            Some(p) => p.clone(),
            None => paths::central_config_file_path(&self.env),
        };
        if !path.exists() {
            return Ok(None);
        }
        let text = std::fs::read_to_string(&path).map_err(ConfigError::ReadFile)?;
        let doc: Value = serde_yaml::from_str(&text).map_err(ConfigError::ParseYaml)?;
        Ok((!doc.is_null()).then_some(doc))
    }
}

/// Load [`CentralConfig`] from the process environment and discovered YAML.
///
/// # Errors
/// See [`ConfigLoader::load`].
pub fn load_central_config() -> Result<CentralConfig, ConfigError> {
    ConfigLoader::new().load()
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

/// Rename a `sandbox.provider` key to `default_provider` (`loader.py:125-127`).
/// `provider` is always removed (matching the Python `sandbox.pop("provider")`),
/// and promoted to `default_provider` only when the latter is absent — so a
/// stray `provider` never trips `deny_unknown_fields`.
fn apply_provider_alias(merged: &mut Value) {
    let Some(sandbox) = merged
        .as_mapping_mut()
        .and_then(|m| m.get_mut("sandbox"))
        .and_then(Value::as_mapping_mut)
    else {
        return;
    };
    if let Some(provider) = sandbox.remove("provider") {
        if !sandbox.contains_key("default_provider") {
            sandbox.insert(Value::String("default_provider".to_owned()), provider);
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use std::collections::BTreeMap;

    use super::*;
    use crate::sandbox::SandboxProvider;

    fn env(pairs: &[(&str, &str)]) -> EnvMap {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
            .collect()
    }

    /// Write a uniquely-named temp YAML file (no external tempfile dep). Each
    /// test uses a distinct name so parallel runs do not collide.
    fn temp_yaml(name: &str, content: &str) -> PathBuf {
        let path = std::env::temp_dir().join(name);
        std::fs::write(&path, content).unwrap();
        path
    }

    /// A guaranteed-absent YAML path. Tests that exercise only the env/init
    /// layers point at this so they never hit central-config discovery (which
    /// could otherwise read a real repo-root `ephemeralos.yaml`).
    fn no_yaml() -> PathBuf {
        let path = std::env::temp_dir().join("eos_config_intentionally_absent.yaml");
        let _ = std::fs::remove_file(&path);
        path
    }

    // AC-eos-config-01: precedence is init > env > yaml > default, and a layer
    // that does not set a field leaves the lower layer's value intact.
    #[test]
    fn test_precedence_init_over_env_over_yaml() {
        let yaml = temp_yaml(
            "eos_config_test_precedence.yaml",
            "sandbox:\n  timeout_s: 10\n  runtime_client_timeout_s: 20\n",
        );
        let init: Value = serde_yaml::from_str("sandbox:\n  timeout_s: 40\n").unwrap();
        let cfg = ConfigLoader::new()
            .yaml_path(&yaml)
            .env(env(&[("EOS__SANDBOX__TIMEOUT_S", "30")]))
            .init(init)
            .load()
            .unwrap();

        assert_eq!(
            cfg.sandbox.timeout_s, 40.0,
            "init must win over env and yaml"
        );
        assert_eq!(
            cfg.sandbox.runtime_client_timeout_s, 20.0,
            "yaml-only field survives"
        );
        assert!(cfg.database.wal, "untouched field keeps its default");
        let _ = std::fs::remove_file(&yaml);
    }

    // AC-eos-config-08: an unknown key fails deserialization (extra="forbid").
    #[test]
    fn test_unknown_yaml_key_rejected() {
        let init: Value = serde_yaml::from_str("sandbox:\n  bogus: true\n").unwrap();
        let result = ConfigLoader::new()
            .yaml_path(no_yaml())
            .env(BTreeMap::new())
            .init(init)
            .load();
        assert!(matches!(result, Err(ConfigError::ParseYaml(_))));
    }

    // AC-eos-config-09: a `sandbox.provider` key aliases to `default_provider`
    // when the latter is absent; a non-Docker value is rejected.
    #[test]
    fn test_provider_key_aliases_to_default_provider() {
        let cfg = ConfigLoader::new()
            .yaml_path(no_yaml())
            .env(env(&[("EOS__SANDBOX__PROVIDER", "docker")]))
            .load()
            .unwrap();
        assert_eq!(cfg.sandbox.default_provider, SandboxProvider::Docker);

        let rejected = ConfigLoader::new()
            .yaml_path(no_yaml())
            .env(env(&[("EOS__SANDBOX__PROVIDER", "daytona")]))
            .load();
        assert!(matches!(rejected, Err(ConfigError::ParseYaml(_))));
    }

    // Subtle risk (§8 ordering note): a legacy var beats an EOS__ var on the
    // same path. No dedicated AC covers this counterintuitive ordering.
    #[test]
    fn test_legacy_env_beats_eos_nested_on_same_path() {
        let cfg = ConfigLoader::new()
            .yaml_path(no_yaml())
            .env(env(&[
                ("EOS__DATABASE__URL", "sqlite:///./from_eos.db"),
                ("EPHEMERALOS_DATABASE_URL", "sqlite:///./from_legacy.db"),
            ]))
            .load()
            .unwrap();
        assert_eq!(cfg.database.url.as_str(), "sqlite:///./from_legacy.db");
    }

    // Subtle risk (provider alias always pops): with both the EOS__ provider
    // alias key and the legacy default_provider var set, the stray `provider`
    // key must be removed so deny_unknown_fields does not fire.
    #[test]
    fn test_provider_alias_pops_even_when_default_present() {
        let cfg = ConfigLoader::new()
            .yaml_path(no_yaml())
            .env(env(&[
                ("EOS__SANDBOX__PROVIDER", "docker"),
                ("EOS_SANDBOX_PROVIDER", "docker"),
            ]))
            .load()
            .unwrap();
        assert_eq!(cfg.sandbox.default_provider, SandboxProvider::Docker);
    }
}
