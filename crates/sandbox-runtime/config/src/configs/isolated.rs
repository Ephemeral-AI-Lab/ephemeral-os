//! Typed schema for the isolated network section of `eos-sandbox/config/prd.yml`.
//!
//! The daemon loads this through `config` and injects it into the isolated
//! workspace lifecycle.

use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::configs::validate::{
    require_absolute, require_f64_at_least, require_f64_gt, require_ratio, require_u32_at_least,
    require_u64_at_least, ConfigFieldError,
};

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IsolatedNetworkConfig {
    pub scratch_root: PathBuf,
    pub ttl_s: f64,
    pub total_cap: u32,
    pub upperdir_bytes: u64,
    pub memavail_fraction: f64,
    pub setup_timeout_s: f64,
    pub exit_grace_s: f64,
    pub rfc1918_egress: Rfc1918Egress,
    pub sample_interval_s: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Rfc1918Egress {
    Allow,
    Deny,
}

impl Default for IsolatedNetworkConfig {
    /// Defaults used when no `isolated` section is injected.
    fn default() -> Self {
        Self {
            scratch_root: PathBuf::from("/eos/scratch/isolated"),
            ttl_s: 1800.0,
            total_cap: 5,
            upperdir_bytes: 1_073_741_824,
            memavail_fraction: 0.5,
            setup_timeout_s: 30.0,
            exit_grace_s: 0.25,
            rfc1918_egress: Rfc1918Egress::Allow,
            sample_interval_s: 0.5,
        }
    }
}

impl IsolatedNetworkConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates isolated-workspace runtime policy.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        require_absolute(&self.scratch_root, "isolated.scratch_root")?;
        require_f64_gt(self.ttl_s, 0.0, "isolated.ttl_s")?;
        require_u32_at_least(self.total_cap, 1, "isolated.total_cap")?;
        require_u64_at_least(self.upperdir_bytes, 1, "isolated.upperdir_bytes")?;
        require_ratio(self.memavail_fraction, "isolated.memavail_fraction")?;
        require_f64_gt(self.setup_timeout_s, 0.0, "isolated.setup_timeout_s")?;
        require_f64_at_least(self.exit_grace_s, 0.0, "isolated.exit_grace_s")?;
        reject_dangerous_scratch_root(&self.scratch_root)?;
        if self.sample_interval_s.is_finite() && self.sample_interval_s >= 0.01 {
            Ok(())
        } else {
            Err(ConfigFieldError::new(
                "isolated.sample_interval_s",
                "must be at least 0.01",
            ))
        }
    }
}

fn reject_dangerous_scratch_root(scratch_root: &Path) -> Result<(), ConfigFieldError> {
    if is_filesystem_root(scratch_root) {
        return Err(ConfigFieldError::new(
            "isolated.scratch_root",
            "must not be the filesystem root",
        ));
    }
    Ok(())
}

fn is_filesystem_root(path: &Path) -> bool {
    path.parent().is_none()
        || path
            .canonicalize()
            .ok()
            .is_some_and(|canonical| canonical.parent().is_none())
}
