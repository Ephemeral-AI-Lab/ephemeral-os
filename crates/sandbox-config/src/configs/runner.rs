//! Typed schema for the runner section of `eos-sandbox/config/prd.yml`.
//!
//! The namespace runner loads this section for mount masking. Runtime
//! environment policy is still wired by the existing runner helpers until the
//! config-infra wiring phase.

use std::path::PathBuf;

use serde::Deserialize;

use crate::configs::validate::{require_absolute, ConfigFieldError};

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunnerConfig {
    pub mount_mask: RunnerMountMaskConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunnerMountMaskConfig {
    pub hidden_paths: Vec<PathBuf>,
}

impl RunnerConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates runner policy.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        if self.mount_mask.hidden_paths.is_empty() {
            return Err(ConfigFieldError::new(
                "runner.mount_mask.hidden_paths",
                "must not be empty",
            ));
        }
        for path in &self.mount_mask.hidden_paths {
            require_absolute(path, "runner.mount_mask.hidden_paths")?;
        }
        Ok(())
    }
}
