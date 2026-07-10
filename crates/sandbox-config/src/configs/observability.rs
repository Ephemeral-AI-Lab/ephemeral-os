//! Typed schema for the `observability` section of `eos-sandbox/config/prd.yml`.
//!
//! `sandbox-config` owns deserialization only. The daemon reads this section and
//! maps `enabled`, `max_line_bytes`, and the sampling budget plus
//! `record::proc::DAEMON` into leaf-owned types, keeping `max_file_bytes` as
//! daemon-owned rotation policy and `views` as daemon-owned view limits. The
//! `sandbox-observability` leaf never imports this crate.

use serde::Deserialize;

use crate::configs::validate::{require_u64_at_least, require_usize_at_least, ConfigFieldError};

/// Default rotation threshold: ~8 MiB. With one rotated sibling the log holds
/// ≈ `2 × max_file_bytes`, sized so a max-window resource query stays answerable.
const DEFAULT_MAX_FILE_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservabilityConfig {
    /// Whether the in-sandbox daemon emits spans/events/samples. Default on.
    #[serde(default = "default_true")]
    pub enabled: bool,
    /// Soft size cap that triggers daemon-owned rotation of the log.
    #[serde(default = "default_max_file_bytes")]
    pub max_file_bytes: u64,
    /// Per-record NDJSON line cap; oversized `attrs`/`metrics` truncate.
    #[serde(default = "default_max_line_bytes")]
    pub max_line_bytes: usize,
    #[serde(default)]
    pub sampling: SamplingConfig,
    #[serde(default)]
    pub views: ViewsConfig,
}

impl Default for ObservabilityConfig {
    fn default() -> Self {
        Self {
            enabled: default_true(),
            max_file_bytes: default_max_file_bytes(),
            max_line_bytes: default_max_line_bytes(),
            sampling: SamplingConfig::default(),
            views: ViewsConfig::default(),
        }
    }
}

impl ObservabilityConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates observability policy.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        require_u64_at_least(self.max_file_bytes, 1, "observability.max_file_bytes")?;
        require_usize_at_least(self.max_line_bytes, 1, "observability.max_line_bytes")?;
        require_usize_at_least(
            self.sampling.max_walk_nodes,
            1,
            "observability.sampling.max_walk_nodes",
        )?;
        require_usize_at_least(
            self.sampling.max_walk_depth,
            1,
            "observability.sampling.max_walk_depth",
        )?;
        require_u64_at_least(
            self.views.resource_window_ms,
            1,
            "observability.views.resource_window_ms",
        )?;
        require_usize_at_least(
            self.views.layer_delta_default_limit,
            1,
            "observability.views.layer_delta_default_limit",
        )?;
        require_usize_at_least(
            self.views.layer_delta_max_limit,
            1,
            "observability.views.layer_delta_max_limit",
        )?;
        if self.views.layer_delta_default_limit > self.views.layer_delta_max_limit {
            return Err(ConfigFieldError::new(
                "observability.views.layer_delta_default_limit",
                "must not exceed layer_delta_max_limit",
            ));
        }
        Ok(())
    }
}

/// One walk budget governs both the upperdir and layer-store samplers
/// (spec decision 8); diverging them needs a measured reason.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct SamplingConfig {
    pub max_walk_nodes: usize,
    pub max_walk_depth: usize,
}

impl Default for SamplingConfig {
    fn default() -> Self {
        Self {
            max_walk_nodes: 1024,
            max_walk_depth: 64,
        }
    }
}

/// Daemon-owned observability view limits.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ViewsConfig {
    /// Maximum resource/trend lookback window honored by the views.
    pub resource_window_ms: u64,
    /// Layer-delta entries returned when the caller names no limit.
    pub layer_delta_default_limit: usize,
    /// Hard cap on caller-requested layer-delta entries.
    pub layer_delta_max_limit: usize,
}

impl Default for ViewsConfig {
    fn default() -> Self {
        Self {
            resource_window_ms: 600_000,
            layer_delta_default_limit: 500,
            layer_delta_max_limit: 5_000,
        }
    }
}

fn default_true() -> bool {
    true
}

fn default_max_file_bytes() -> u64 {
    DEFAULT_MAX_FILE_BYTES
}

fn default_max_line_bytes() -> usize {
    16 * 1024
}
