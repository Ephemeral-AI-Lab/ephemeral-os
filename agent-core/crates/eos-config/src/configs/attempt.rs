//! Per-Attempt run-stage tunables: the cap on concurrently-launched agent runs
//! in an Attempt's RUN stage.
//!
//! `eos-workflow` has no `eos-config` edge, so the value does not flow directly:
//! `eos-runtime` reads `workflow.attempt.max_concurrent_task_runs` and injects it
//! into the per-attempt deps as a plain `usize`.

use serde::{Deserialize, Serialize};

use crate::error::ConfigError;

/// Per-Attempt run-stage tunables.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct AttemptConfig {
    /// Per-Attempt cap on concurrently-launched generator/reducer agent runs.
    /// Range-checked `>= 1` by [`AttemptConfig::validate`].
    pub max_concurrent_task_runs: usize,
}

impl Default for AttemptConfig {
    fn default() -> Self {
        Self {
            max_concurrent_task_runs: 8,
        }
    }
}

impl AttemptConfig {
    /// Enforce numeric-range constraints (call after deserializing a section).
    ///
    /// # Errors
    /// Returns [`ConfigError::OutOfRange`] when `max_concurrent_task_runs < 1`.
    pub fn validate(&self) -> Result<(), ConfigError> {
        self.validate_with_field("attempt.max_concurrent_task_runs")
    }

    pub(crate) fn validate_with_field(&self, field: &str) -> Result<(), ConfigError> {
        if self.max_concurrent_task_runs < 1 {
            return Err(ConfigError::OutOfRange {
                field: field.to_owned(),
                detail: "must be >= 1".to_owned(),
            });
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_are_set() {
        assert_eq!(AttemptConfig::default().max_concurrent_task_runs, 8);
    }
}
