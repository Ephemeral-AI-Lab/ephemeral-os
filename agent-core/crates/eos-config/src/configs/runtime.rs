//! Agent runtime tunables owned by `eos-runtime`.

use serde::{Deserialize, Serialize};

use crate::error::ConfigError;

/// Agent runtime configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct RuntimeConfig {
    /// Poll interval for `api.v1.command.collect_completed` heartbeat calls.
    pub command_session_completion_poll_interval_ms: u64,
}

impl RuntimeConfig {
    /// Build runtime config from the file-backed poll interval value.
    #[must_use]
    pub const fn new(command_session_completion_poll_interval_ms: u64) -> Self {
        Self {
            command_session_completion_poll_interval_ms,
        }
    }

    /// Enforce numeric-range constraints.
    ///
    /// # Errors
    /// Returns [`ConfigError::OutOfRange`] when the poll interval is zero.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.command_session_completion_poll_interval_ms == 0 {
            return Err(ConfigError::OutOfRange {
                field: "runtime.command_session_completion_poll_interval_ms".to_owned(),
                detail: "must be >= 1".to_owned(),
            });
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;

    #[test]
    fn parses_poll_interval() {
        let cfg: RuntimeConfig =
            serde_yaml::from_str("command_session_completion_poll_interval_ms: 250\n").unwrap();

        assert_eq!(cfg.command_session_completion_poll_interval_ms, 250);
        cfg.validate().unwrap();
    }

    #[test]
    fn rejects_zero_poll_interval() {
        let cfg = RuntimeConfig::new(0);

        let err = cfg.validate().unwrap_err();
        assert!(matches!(
            err,
            ConfigError::OutOfRange { field, .. }
                if field == "runtime.command_session_completion_poll_interval_ms"
        ));
    }
}
