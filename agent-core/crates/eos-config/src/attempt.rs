//! Per-Attempt run-stage tunables. This section is Rust-only: it has no Python
//! equivalent (the Python attempt launcher spawned every ready run unbounded via
//! `loop.create_task`). It is the configurable home of the per-Attempt fan-out
//! cap consumed by `eos-workflow` (impl-eos-workflow.md §7).
//!
//! `eos-workflow` has no `eos-config` edge, so the value does not flow directly:
//! `eos-runtime` reads `config.attempt.max_concurrent_task_runs` and injects it
//! into the per-attempt deps as a plain `usize`.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Per-Attempt run-stage tunables.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct AttemptConfig {
    /// Per-Attempt cap on concurrently-launched generator/reducer agent runs.
    /// Range-checked `>= 1`.
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
    pub fn validate(&self) -> Result<(), crate::error::ConfigError> {
        if self.max_concurrent_task_runs < 1 {
            return Err(crate::error::ConfigError::OutOfRange {
                field: "attempt.max_concurrent_task_runs".to_owned(),
                detail: "must be >= 1".to_owned(),
            });
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-eos-config-02 (attempt subset): the Rust-only default is 8.
    #[test]
    fn test_attempt_defaults() {
        assert_eq!(AttemptConfig::default().max_concurrent_task_runs, 8);
    }
}
