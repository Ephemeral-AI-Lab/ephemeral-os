//! Typed schema for the isolated workspace section of `sandbox/config/prd.yml`.
//!
//! The daemon loads this through `eos-config` and injects it into the isolated
//! workspace lifecycle.

use std::path::{Path, PathBuf};

use serde::Deserialize;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IsolatedWorkspaceConfig {
    pub enabled: bool,
    pub scratch_root: PathBuf,
    pub audit_jsonl_path: PathBuf,
    pub ttl_s: f64,
    pub total_cap: u32,
    pub upperdir_bytes: u64,
    pub memavail_fraction: f64,
    pub setup_timeout_s: f64,
    pub exit_grace_s: f64,
    pub rfc1918_egress: Rfc1918Egress,
    pub fallback_dns: String,
    pub workspace_root: PathBuf,
    pub sample_interval_s: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Rfc1918Egress {
    Allow,
    Deny,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("{field}: {reason}")]
pub struct IsolatedConfigError {
    field: &'static str,
    reason: &'static str,
}

impl IsolatedWorkspaceConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates isolated-workspace runtime policy.
    pub fn validate(&self) -> Result<(), IsolatedConfigError> {
        require_absolute(&self.scratch_root, "isolated_workspace.scratch_root")?;
        require_absolute(
            &self.audit_jsonl_path,
            "isolated_workspace.audit_jsonl_path",
        )?;
        require_f64_gt(self.ttl_s, 0.0, "isolated_workspace.ttl_s")?;
        if self.enabled {
            require_u32_at_least(self.total_cap, 1, "isolated_workspace.total_cap")?;
        }
        require_u64_at_least(self.upperdir_bytes, 1, "isolated_workspace.upperdir_bytes")?;
        require_ratio(
            self.memavail_fraction,
            "isolated_workspace.memavail_fraction",
        )?;
        require_f64_gt(
            self.setup_timeout_s,
            0.0,
            "isolated_workspace.setup_timeout_s",
        )?;
        require_f64_at_least(self.exit_grace_s, 0.0, "isolated_workspace.exit_grace_s")?;
        require_non_empty(&self.fallback_dns, "isolated_workspace.fallback_dns")?;
        require_absolute(&self.workspace_root, "isolated_workspace.workspace_root")?;
        if self.sample_interval_s.is_finite() && self.sample_interval_s >= 0.01 {
            Ok(())
        } else {
            Err(invalid(
                "isolated_workspace.sample_interval_s",
                "must be at least 0.01",
            ))
        }
    }
}

fn require_absolute(path: &Path, field: &'static str) -> Result<(), IsolatedConfigError> {
    if path.is_absolute() {
        Ok(())
    } else {
        Err(invalid(field, "must be an absolute path"))
    }
}

fn require_non_empty(value: &str, field: &'static str) -> Result<(), IsolatedConfigError> {
    if value.trim().is_empty() {
        Err(invalid(field, "must be non-empty"))
    } else {
        Ok(())
    }
}

fn require_u32_at_least(
    value: u32,
    minimum: u32,
    field: &'static str,
) -> Result<(), IsolatedConfigError> {
    if value >= minimum {
        Ok(())
    } else {
        Err(invalid(field, "must be at least 1"))
    }
}

fn require_u64_at_least(
    value: u64,
    minimum: u64,
    field: &'static str,
) -> Result<(), IsolatedConfigError> {
    if value >= minimum {
        Ok(())
    } else {
        Err(invalid(field, "must be at least 1"))
    }
}

fn require_f64_gt(
    value: f64,
    minimum: f64,
    field: &'static str,
) -> Result<(), IsolatedConfigError> {
    if value.is_finite() && value > minimum {
        Ok(())
    } else {
        Err(invalid(field, "must be greater than zero"))
    }
}

fn require_f64_at_least(
    value: f64,
    minimum: f64,
    field: &'static str,
) -> Result<(), IsolatedConfigError> {
    if value.is_finite() && value >= minimum {
        Ok(())
    } else {
        Err(invalid(field, "must be at least zero"))
    }
}

fn require_ratio(value: f64, field: &'static str) -> Result<(), IsolatedConfigError> {
    if value.is_finite() && value > 0.0 && value <= 1.0 {
        Ok(())
    } else {
        Err(invalid(field, "must be greater than 0.0 and at most 1.0"))
    }
}

fn invalid(field: &'static str, reason: &'static str) -> IsolatedConfigError {
    IsolatedConfigError { field, reason }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_prd_isolated_workspace_section_deserializes_and_validates() {
        prd_config()
            .validate()
            .expect("prd isolated workspace config is valid");
    }

    #[test]
    fn config_validation_rejects_invalid_isolated_values() {
        let mut cfg = prd_config();
        cfg.scratch_root = PathBuf::from("relative");
        assert_invalid(cfg, "isolated_workspace.scratch_root");

        let mut cfg = prd_config();
        cfg.enabled = true;
        cfg.total_cap = 0;
        assert_invalid(cfg, "isolated_workspace.total_cap");

        let mut cfg = prd_config();
        cfg.memavail_fraction = 0.0;
        assert_invalid(cfg, "isolated_workspace.memavail_fraction");

        let mut cfg = prd_config();
        cfg.exit_grace_s = -0.1;
        assert_invalid(cfg, "isolated_workspace.exit_grace_s");

        let mut cfg = prd_config();
        cfg.sample_interval_s = 0.001;
        assert_invalid(cfg, "isolated_workspace.sample_interval_s");
    }

    fn prd_config() -> IsolatedWorkspaceConfig {
        crate::load_prd()
            .expect("prd config loads")
            .section("isolated_workspace")
            .expect("isolated_workspace section deserializes")
    }

    fn assert_invalid(config: IsolatedWorkspaceConfig, field: &str) {
        let err = config.validate().expect_err("config should be invalid");
        let message = err.to_string();
        assert!(message.contains(field), "{message}");
    }
}
