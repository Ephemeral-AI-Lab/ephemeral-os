//! Backend-owned sandbox lifecycle and provisioning defaults.

use serde::{Deserialize, Serialize};

use crate::loader::ConfigError;

/// Fresh-sandbox defaults and cleanup policy. These belong to backend-server,
/// not agent-core, and are passed to the `SandboxManager` / provisioner.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct SandboxConfig {
    /// Default snapshot to provision from when a request supplies none.
    pub default_snapshot: Option<String>,
    /// Upper bound on concurrently backend-owned sandboxes.
    pub max_owned_sandboxes: usize,
    /// Destroy a sandbox once its last reference is released.
    pub destroy_on_finish: bool,
    /// Sandbox startup timeout in milliseconds.
    pub startup_timeout_ms: u64,
}

impl SandboxConfig {
    /// Enforce numeric-range constraints.
    ///
    /// # Errors
    /// [`ConfigError::OutOfRange`] when `max_owned_sandboxes` or
    /// `startup_timeout_ms` is zero.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.max_owned_sandboxes == 0 {
            return Err(ConfigError::OutOfRange {
                field: "sandbox.max_owned_sandboxes",
                detail: "must be >= 1",
            });
        }
        if self.startup_timeout_ms == 0 {
            return Err(ConfigError::OutOfRange {
                field: "sandbox.startup_timeout_ms",
                detail: "must be >= 1",
            });
        }
        Ok(())
    }
}
