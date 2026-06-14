//! Typed schema for the isolated workspace section of `eos-sandbox/config/prd.yml`.
//!
//! The daemon loads this through `config` and injects it into the isolated
//! workspace lifecycle.

use std::path::PathBuf;

use serde::Deserialize;

use crate::configs::validate::{
    require_absolute, require_f64_at_least, require_f64_gt, require_non_empty, require_ratio,
    require_u32_at_least, require_u64_at_least, ConfigFieldError,
};

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IsolatedWorkspaceConfig {
    pub enabled: bool,
    pub scratch_root: PathBuf,
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

impl Default for IsolatedWorkspaceConfig {
    /// Disabled-by-default fallbacks used when no `isolated_workspace` section
    /// is injected (matches `eos-sandbox/config/prd.yml`).
    fn default() -> Self {
        Self {
            enabled: false,
            scratch_root: PathBuf::from("/eos/scratch/isolated"),
            ttl_s: 1800.0,
            total_cap: 5,
            upperdir_bytes: 1_073_741_824,
            memavail_fraction: 0.5,
            setup_timeout_s: 30.0,
            exit_grace_s: 0.25,
            rfc1918_egress: Rfc1918Egress::Allow,
            fallback_dns: "1.1.1.1".to_owned(),
            workspace_root: PathBuf::from("/testbed"),
            sample_interval_s: 0.5,
        }
    }
}

impl IsolatedWorkspaceConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates isolated-workspace runtime policy.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        require_absolute(&self.scratch_root, "isolated_workspace.scratch_root")?;
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
            Err(ConfigFieldError::new(
                "isolated_workspace.sample_interval_s",
                "must be at least 0.01",
            ))
        }
    }
}

#[cfg(test)]
#[path = "../../tests/unit/configs/isolated_workspace.rs"]
mod tests;
