use serde::de::DeserializeOwned;
use serde::de::IntoDeserializer;
use serde_yaml::{Mapping, Value};

use std::fs;
use std::path::Path;

use crate::error::ConfigError;
use crate::merge;

/// Parsed sandbox configuration document.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfigDocument {
    value: Value,
}

impl ConfigDocument {
    pub(crate) fn read(path: &Path) -> Result<Self, ConfigError> {
        let text = fs::read_to_string(path).map_err(|source| ConfigError::Read {
            path: path.to_path_buf(),
            source,
        })?;
        Self::parse(path, &text)
    }

    pub(crate) fn parse(path: &Path, text: &str) -> Result<Self, ConfigError> {
        let value = serde_yaml::from_str(text).map_err(|source| ConfigError::Parse {
            path: path.to_path_buf(),
            source,
        })?;
        Ok(Self { value })
    }

    #[cfg(test)]
    pub(crate) fn from_yaml_str(text: &str) -> Result<Self, ConfigError> {
        Self::parse(Path::new("<test>"), text)
    }

    pub(crate) fn merge(&mut self, override_doc: Self) -> Result<(), ConfigError> {
        merge::ensure_mapping_root(&self.value)?;
        merge::merge_values(&mut self.value, override_doc.value)?;
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn into_value(self) -> Value {
        self.value
    }

    /// Deserialize a top-level section into its owner crate's typed schema.
    ///
    /// # Errors
    /// Returns an error if the section is missing or if typed deserialization
    /// fails.
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
        let deserializer = section.clone().into_deserializer();
        serde_path_to_error::deserialize(deserializer).map_err(|source| {
            ConfigError::DeserializeSection {
                section: name.to_owned(),
                source,
            }
        })
    }

    fn root_mapping(&self) -> Result<&Mapping, ConfigError> {
        match &self.value {
            Value::Mapping(mapping) => Ok(mapping),
            _ => Err(ConfigError::InvalidDocumentRoot),
        }
    }
}
