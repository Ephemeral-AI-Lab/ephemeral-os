//! The loaded, merged config document. Runtime owns loading and merge only; each
//! owner crate owns its typed section schema and deserializes it on demand via
//! [`ConfigDocument::section`]. This is the seam that replaces the former
//! `CentralConfig` aggregate: there is no central composition-root struct.

use serde::de::DeserializeOwned;
use serde_yaml::{Mapping, Value};

use eos_types::ConfigError;

/// A loaded, merged configuration document (`prd.yml` overlaid by `local.yml`).
/// Sections are deserialized into their owning typed schema on demand.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfigDocument {
    value: Value,
}

impl ConfigDocument {
    /// Wrap a merged YAML value.
    pub(crate) fn from_value(value: Value) -> Self {
        Self { value }
    }

    /// Deserialize a top-level section into its owning crate's typed schema.
    ///
    /// Range/contradiction checks are the section type's own `validate()`
    /// responsibility; this only deserializes (where `deny_unknown_fields` and
    /// the owner-local config parse surfaces).
    ///
    /// # Errors
    /// Returns [`ConfigError::MissingSection`] when the section is absent, or
    /// [`ConfigError::ParseYaml`] when typed deserialization fails.
    ///
    pub fn section<T>(&self, name: &str) -> Result<T, ConfigError>
    where
        T: DeserializeOwned,
    {
        let section = self
            .root_mapping()?
            .get(Value::String(name.to_owned()))
            .ok_or_else(|| ConfigError::MissingSection {
                section: name.to_owned(),
            })?;
        serde_yaml::from_value(section.clone()).map_err(ConfigError::ParseYaml)
    }

    fn root_mapping(&self) -> Result<&Mapping, ConfigError> {
        match &self.value {
            Value::Mapping(mapping) => Ok(mapping),
            _ => Err(ConfigError::InvalidDocumentRoot),
        }
    }
}
