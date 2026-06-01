//! Daemon-to-plugin-harness refresh protocol.
//!
//! The daemon is authoritative for LayerStack freshness. A read-only service
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
mod tests {
    use super::*;

    #[test]
    fn ack_rejects_wrong_manifest_key() {
        let ack = RefreshAck {
            manifest_key: "old".to_owned(),
            accepted: true,
            reason: None,
        };
        assert!(matches!(
            ack.require_manifest("new"),
            Err(PluginError::ProjectionStale(message)) if message.contains("expected new")
        ));
    }

    #[test]
    fn swap_workspace_reports_target_manifest() {
        let request = RefreshRequest::SwapWorkspace {
            layer_paths: vec!["/layers/a".to_owned()],
            workspace_root: "/eos/plugin/workspace".to_owned(),
            manifest_key: "root@2".to_owned(),
        };
        assert_eq!(request.manifest_key(), Some("root@2"));
    }
}
