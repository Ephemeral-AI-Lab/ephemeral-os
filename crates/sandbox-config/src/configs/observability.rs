//! Typed schema for the `observability` section of `eos-sandbox/config/prd.yml`.
//!
//! `sandbox-config` owns deserialization only. The daemon reads this section and
//! maps `enabled` plus `record::proc::DAEMON` into the leaf-owned `ObserverConfig`,
//! keeping `max_file_bytes` as daemon-owned rotation policy. The
//! `sandbox-observability` leaf never imports this crate.

use serde::Deserialize;

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
}

impl Default for ObservabilityConfig {
    fn default() -> Self {
        Self {
            enabled: default_true(),
            max_file_bytes: default_max_file_bytes(),
        }
    }
}

fn default_true() -> bool {
    true
}

fn default_max_file_bytes() -> u64 {
    DEFAULT_MAX_FILE_BYTES
}
