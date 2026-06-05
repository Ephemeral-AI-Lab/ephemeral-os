//! Fail-fast range checks (`validate`). Network-url rejection happens earlier,
//! in [`DatabaseUrl::parse`]; this module covers the Pydantic `ge`/`gt` numeric
//! ranges the Rust types do not already enforce (§8 item 9).
//!
//! [`DatabaseUrl::parse`]: crate::DatabaseUrl::parse

use crate::config::CentralConfig;
use crate::error::ConfigError;

/// Validate numeric-range constraints.
///
/// # Errors
/// Returns [`ConfigError::OutOfRange`] for an out-of-range numeric field.
pub(crate) fn validate(cfg: &CentralConfig) -> Result<(), ConfigError> {
    // Pydantic ge/gt parity — only the constraints the Rust type does not give.
    if cfg.database.pool_size < 1 {
        return Err(ConfigError::OutOfRange {
            field: "database.pool_size".to_owned(),
            detail: "must be >= 1".to_owned(),
        });
    }
    let r = &cfg.providers.retry;
    if r.base_delay_s < 0.0 || r.max_delay_s < 0.0 {
        return Err(ConfigError::OutOfRange {
            field: "providers.retry.*delay_s".to_owned(),
            detail: "must be >= 0".to_owned(),
        });
    }
    if cfg.attempt.max_concurrent_task_runs < 1 {
        return Err(ConfigError::OutOfRange {
            field: "attempt.max_concurrent_task_runs".to_owned(),
            detail: "must be >= 1".to_owned(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-eos-config-11: out-of-range numeric fields are rejected; in-range pass.
    #[test]
    fn test_range_constraints_rejected() {
        assert!(validate(&CentralConfig::default()).is_ok());

        let mut c = CentralConfig::default();
        c.database.pool_size = 0;
        assert!(matches!(validate(&c), Err(ConfigError::OutOfRange { .. })));

        let mut c = CentralConfig::default();
        c.providers.retry.base_delay_s = -1.0;
        assert!(matches!(validate(&c), Err(ConfigError::OutOfRange { .. })));

        let mut c = CentralConfig::default();
        c.attempt.max_concurrent_task_runs = 0;
        assert!(matches!(validate(&c), Err(ConfigError::OutOfRange { .. })));
    }
}
