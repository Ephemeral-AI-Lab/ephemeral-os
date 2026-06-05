//! Provider runtime configuration. The retry defaults here are the single
//! source of truth consumed by `eos-llm-client` (GC-eos-config-04); the crate
//! keeps no local retry constants.

use std::collections::BTreeSet;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// Provider retry policy (`sections/providers.py:17-23`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct RetryConfig {
    /// Maximum retry attempts. `>= 0` is already enforced by `u32`.
    pub max_retries: u32,
    /// Initial backoff delay in seconds. Range-checked `>= 0`.
    pub base_delay_s: f64,
    /// Maximum backoff delay in seconds. Range-checked `>= 0`.
    pub max_delay_s: f64,
    /// HTTP status codes that trigger a retry. A [`BTreeSet`] (not a hash set)
    /// for deterministic serialized ordering.
    pub status_codes: BTreeSet<u16>,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self {
            max_retries: 3,
            base_delay_s: 1.0,
            max_delay_s: 30.0,
            status_codes: [429, 500, 502, 503, 529].into_iter().collect(),
        }
    }
}

impl RetryConfig {
    /// Enforce numeric-range constraints (call after deserializing a section).
    ///
    /// # Errors
    /// Returns [`ConfigError::OutOfRange`] when a delay is negative.
    pub fn validate(&self) -> Result<(), crate::error::ConfigError> {
        if self.base_delay_s < 0.0 || self.max_delay_s < 0.0 {
            return Err(crate::error::ConfigError::OutOfRange {
                field: "providers.retry.*delay_s".to_owned(),
                detail: "must be >= 0".to_owned(),
            });
        }
        Ok(())
    }
}

/// Provider-level runtime configuration (`sections/providers.py:33-37`).
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct ProvidersConfig {
    /// Retry policy applied across providers.
    pub retry: RetryConfig,
}

impl ProvidersConfig {
    /// Validate nested provider sections.
    ///
    /// # Errors
    /// Propagates [`RetryConfig::validate`].
    pub fn validate(&self) -> Result<(), crate::error::ConfigError> {
        self.retry.validate()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-eos-config-02 (retry subset): defaults match the Python source.
    #[test]
    fn test_retry_defaults() {
        let r = RetryConfig::default();
        assert_eq!(r.max_retries, 3);
        assert_eq!(r.base_delay_s, 1.0);
        assert_eq!(r.max_delay_s, 30.0);
        assert_eq!(
            r.status_codes,
            [429u16, 500, 502, 503, 529].into_iter().collect()
        );
    }
}
