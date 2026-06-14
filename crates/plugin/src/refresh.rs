//! Daemon-to-plugin-harness refresh protocol.
//!
//! The daemon is authoritative for `LayerStack` freshness. A read-only service
//! must either acknowledge the target manifest key or fail/restart; stale
//! service state must never answer silently.

use serde::{Deserialize, Serialize};

use crate::error::{PluginError, Result};

/// Request sent by the daemon to a plugin service harness during refresh.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[non_exhaustive]
pub enum RefreshRequest {
    PrepareRefresh {
        target_manifest_key: String,
    },
    Quiesce {
        request_id: String,
    },
    SwapWorkspace {
        layer_paths: Vec<String>,
        workspace_root: String,
        manifest_key: String,
    },
    NotifyRefresh {
        changed_paths: Vec<String>,
        full_resync: bool,
    },
    Resume {
        request_id: String,
    },
    Restart {
        reason: String,
    },
    Health {
        manifest_key: String,
    },
}

impl RefreshRequest {
    /// The manifest key this message targets, when the message carries one.
    #[must_use]
    pub fn manifest_key(&self) -> Option<&str> {
        match self {
            Self::PrepareRefresh {
                target_manifest_key,
            } => Some(target_manifest_key),
            Self::SwapWorkspace { manifest_key, .. } | Self::Health { manifest_key } => {
                Some(manifest_key)
            }
            Self::Quiesce { .. }
            | Self::NotifyRefresh { .. }
            | Self::Resume { .. }
            | Self::Restart { .. } => None,
        }
    }
}

/// Harness acknowledgement for a refresh request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RefreshAck {
    pub manifest_key: String,
    pub accepted: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

impl RefreshAck {
    /// Ensure the service acknowledged the target manifest.
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::ProjectionStale`] when the service rejected the
    /// refresh or acknowledged a different manifest key.
    pub fn require_manifest(&self, target_manifest_key: &str) -> Result<()> {
        if !self.accepted {
            return Err(PluginError::ProjectionStale(
                self.reason
                    .clone()
                    .unwrap_or_else(|| "refresh was rejected".to_owned()),
            ));
        }
        if self.manifest_key != target_manifest_key {
            return Err(PluginError::ProjectionStale(format!(
                "service acknowledged manifest {}, expected {}",
                self.manifest_key, target_manifest_key
            )));
        }
        Ok(())
    }
}

#[cfg(test)]
#[path = "../tests/unit/refresh.rs"]
mod tests;
