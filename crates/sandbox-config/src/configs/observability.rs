//! Typed schema for the `observability` section of `eos-sandbox/config/prd.yml`.
//!
//! `sandbox-config` owns deserialization only. The daemon reads this section and
//! maps `enabled`, the total disk/line bounds, and the sampling budget plus
//! `record::proc::DAEMON` into leaf-owned types. The
//! `sandbox-observability-telemetry` never imports this crate.

use serde::de::Error as _;
use serde::{Deserialize, Deserializer};

use crate::configs::validate::{require_u64_at_least, require_usize_at_least, ConfigFieldError};

const DEFAULT_MAX_DISK_BYTES: u64 = 4 * 1024 * 1024;
const MIN_MAX_DISK_BYTES: u64 = 1024 * 1024;
const MAX_MAX_DISK_BYTES: u64 = 16 * 1024 * 1024;
const MAX_MAX_LINE_BYTES: usize = 16 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObservabilityConfig {
    /// Whether the in-sandbox daemon emits spans/events/samples. Default on.
    pub enabled: bool,
    /// Hard total cap across the active and one rotated event segment.
    pub max_disk_bytes: u64,
    /// Per-record NDJSON line cap; oversized `attrs`/`metrics` truncate.
    pub max_line_bytes: usize,
    pub sampling: SamplingConfig,
    pub views: ViewsConfig,
    /// True only when the compatibility `max_file_bytes` key supplied the cap.
    pub used_legacy_max_file_bytes: bool,
}

impl Default for ObservabilityConfig {
    fn default() -> Self {
        Self {
            enabled: default_true(),
            max_disk_bytes: DEFAULT_MAX_DISK_BYTES,
            max_line_bytes: default_max_line_bytes(),
            sampling: SamplingConfig::default(),
            views: ViewsConfig::default(),
            used_legacy_max_file_bytes: false,
        }
    }
}

#[derive(Deserialize)]
#[serde(default, deny_unknown_fields)]
struct RawObservabilityConfig {
    enabled: bool,
    max_disk_bytes: Option<u64>,
    max_file_bytes: Option<u64>,
    max_line_bytes: usize,
    sampling: SamplingConfig,
    views: ViewsConfig,
}

impl Default for RawObservabilityConfig {
    fn default() -> Self {
        Self {
            enabled: default_true(),
            max_disk_bytes: None,
            max_file_bytes: None,
            max_line_bytes: default_max_line_bytes(),
            sampling: SamplingConfig::default(),
            views: ViewsConfig::default(),
        }
    }
}

impl<'de> Deserialize<'de> for ObservabilityConfig {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let raw = RawObservabilityConfig::deserialize(deserializer)?;
        if raw.max_disk_bytes.is_some() && raw.max_file_bytes.is_some() {
            return Err(D::Error::custom(
                "max_disk_bytes and deprecated max_file_bytes cannot both be set",
            ));
        }
        let used_legacy_max_file_bytes = raw.max_file_bytes.is_some();
        let max_disk_bytes = raw.max_disk_bytes.unwrap_or_else(|| {
            raw.max_file_bytes.map_or(DEFAULT_MAX_DISK_BYTES, |legacy| {
                legacy.saturating_mul(2).min(MAX_MAX_DISK_BYTES)
            })
        });
        Ok(Self {
            enabled: raw.enabled,
            max_disk_bytes,
            max_line_bytes: raw.max_line_bytes,
            sampling: raw.sampling,
            views: raw.views,
            used_legacy_max_file_bytes,
        })
    }
}

impl ObservabilityConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates observability policy.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        require_u64_at_least(
            self.max_disk_bytes,
            MIN_MAX_DISK_BYTES,
            "observability.max_disk_bytes",
        )?;
        if self.max_disk_bytes > MAX_MAX_DISK_BYTES {
            return Err(ConfigFieldError::new(
                "observability.max_disk_bytes",
                "must not exceed 16777216",
            ));
        }
        require_usize_at_least(self.max_line_bytes, 1, "observability.max_line_bytes")?;
        if self.max_line_bytes > MAX_MAX_LINE_BYTES {
            return Err(ConfigFieldError::new(
                "observability.max_line_bytes",
                "must not exceed 16384",
            ));
        }
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

fn default_max_line_bytes() -> usize {
    MAX_MAX_LINE_BYTES
}
