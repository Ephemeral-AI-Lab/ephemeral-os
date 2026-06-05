//! [`CentralConfig`] — the composition-root config struct.
//!
//! `runner` and `engine` from the Python `CentralConfig` are intentionally
//! absent (GC-eos-config-05 runner is test-runner flavored; GC-eos-config-07 the
//! engine section is empty); `attempt` is a Rust-only addition.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::attempt::AttemptConfig;
use crate::database::DatabaseConfig;
use crate::providers::ProvidersConfig;
use crate::sandbox::SandboxConfig;

/// The validated, immutable composition root for all runtime-tunable config.
///
/// Built by [`load_central_config`] (and wrapped in `Arc` by `eos-runtime`); it
/// is read-only after load and holds no secrets, connections, or tasks.
///
/// [`load_central_config`]: crate::load_central_config
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct CentralConfig {
    /// Sqlite database config.
    pub database: DatabaseConfig,
    /// Sandbox provider config.
    pub sandbox: SandboxConfig,
    /// Provider (retry) config.
    pub providers: ProvidersConfig,
    /// Per-Attempt run-stage tunables (Rust-only).
    pub attempt: AttemptConfig,
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-eos-config-02 (composition): the full default matches the Python
    // defaults for the surviving sections plus the Rust-only attempt section.
    #[test]
    fn test_default_config_matches_python() {
        let c = CentralConfig::default();
        assert_eq!(c.database.pool_size, 5);
        assert_eq!(c.providers.retry.max_retries, 3);
        assert_eq!(c.providers.retry.base_delay_s, 1.0);
        assert_eq!(c.providers.retry.max_delay_s, 30.0);
        assert!(c.database.wal);
        assert!(c.database.foreign_keys);
        assert_eq!(c.attempt.max_concurrent_task_runs, 8);
    }
}
