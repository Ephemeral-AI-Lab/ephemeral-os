//! Workflow lifecycle and per-attempt runtime tunables.
//!
//! `eos-agent-core` reads this section at the composition root. The workflow depth
//! bound feeds the planner deferral hook; the nested attempt section feeds
//! `eos-workflow`'s per-attempt launch dependencies as plain values.

use serde::{Deserialize, Serialize};

use eos_types::ConfigError;

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

    fn validate_with_field(&self, field: &str) -> Result<(), ConfigError> {
        if self.max_concurrent_task_runs < 1 {
            return Err(ConfigError::OutOfRange {
                field: field.to_owned(),
                detail: "must be >= 1".to_owned(),
            });
        }
        Ok(())
    }
}

/// Default deepest workflow depth still allowed to defer.
pub const DEFAULT_WORKFLOW_MAX_DEPTH: u32 = 2;

/// Workflow runtime configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct WorkflowConfig {
    /// Deepest workflow depth still allowed to set a deferred goal.
    #[serde(default = "default_workflow_max_depth", rename = "max-depth")]
    pub max_depth: u32,
    /// Per-Attempt run-stage tunables.
    #[serde(default)]
    pub attempt: AttemptConfig,
}

impl Default for WorkflowConfig {
    fn default() -> Self {
        Self {
            max_depth: DEFAULT_WORKFLOW_MAX_DEPTH,
            attempt: AttemptConfig::default(),
        }
    }
}

impl WorkflowConfig {
    /// Enforce numeric-range constraints.
    ///
    /// # Errors
    /// Returns [`ConfigError::OutOfRange`] when `max-depth < 1` or the nested
    /// attempt config is invalid.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.max_depth < 1 {
            return Err(ConfigError::OutOfRange {
                field: "workflow.max-depth".to_owned(),
                detail: "must be >= 1".to_owned(),
            });
        }
        self.attempt
            .validate_with_field("workflow.attempt.max_concurrent_task_runs")
    }
}

const fn default_workflow_max_depth() -> u32 {
    DEFAULT_WORKFLOW_MAX_DEPTH
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn attempt_defaults_are_set() {
        assert_eq!(AttemptConfig::default().max_concurrent_task_runs, 8);
    }

    #[test]
    fn workflow_defaults_are_set() {
        let cfg = WorkflowConfig::default();
        assert_eq!(cfg.max_depth, 2);
        assert_eq!(cfg.attempt.max_concurrent_task_runs, 8);
    }

    #[test]
    fn parses_hyphenated_max_depth() {
        let cfg: WorkflowConfig =
            serde_yaml::from_str("max-depth: 3\nattempt:\n  max_concurrent_task_runs: 4\n")
                .unwrap();

        assert_eq!(cfg.max_depth, 3);
        assert_eq!(cfg.attempt.max_concurrent_task_runs, 4);
    }

    #[test]
    fn rejects_zero_max_depth() {
        let cfg = WorkflowConfig {
            max_depth: 0,
            ..WorkflowConfig::default()
        };

        let err = cfg.validate().unwrap_err();
        assert!(matches!(
            err,
            ConfigError::OutOfRange { field, .. } if field == "workflow.max-depth"
        ));
    }
}
