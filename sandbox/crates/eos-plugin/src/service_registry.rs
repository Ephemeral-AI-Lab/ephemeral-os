//! Logical plugin-service registry.
//!
//! This registry deliberately performs no process I/O. `eos-daemon` wraps this
//! contract with live process, namespace, and PPC management.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::error::{PluginError, Result};
use crate::service::PluginServiceKey;

/// Lifecycle state reported by a plugin service.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum PluginServiceState {
    Starting,
    Ready,
    Refreshing,
    Stale,
    Restarting,
    Stopped,
    Failed,
}

/// Serializable status for `api.plugin.status`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginServiceStatus {
    pub key: PluginServiceKey,
    pub state: PluginServiceState,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub manifest_key: Option<String>,
    #[serde(default)]
    pub registered_ops: Vec<String>,
    #[serde(default)]
    pub refresh_count: u64,
    #[serde(default)]
    pub restart_count: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
}

impl PluginServiceStatus {
    pub fn new(key: PluginServiceKey) -> Self {
        Self {
            key,
            state: PluginServiceState::Starting,
            manifest_key: None,
            registered_ops: Vec::new(),
            refresh_count: 0,
            restart_count: 0,
            last_error: None,
        }
    }

    /// Ensure this status is current before a read-only request answers.
    pub fn require_ready_on_manifest(&self, target_manifest_key: &str) -> Result<()> {
        if self.state != PluginServiceState::Ready {
            return Err(PluginError::ProjectionStale(format!(
                "service is {:?}, not ready",
                self.state
            )));
        }
        if self.manifest_key.as_deref() != Some(target_manifest_key) {
            return Err(PluginError::ProjectionStale(format!(
                "service manifest {:?} is not target {}",
                self.manifest_key, target_manifest_key
            )));
        }
        Ok(())
    }
}

/// Pure registry keyed by [`PluginServiceKey`].
#[derive(Debug, Default)]
pub struct PluginServiceRegistry {
    services: BTreeMap<PluginServiceKey, PluginServiceStatus>,
}

impl PluginServiceRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// Get or insert a service status.
    pub fn ensure(&mut self, key: PluginServiceKey) -> &mut PluginServiceStatus {
        self.services
            .entry(key.clone())
            .or_insert_with(|| PluginServiceStatus::new(key))
    }

    pub fn get(&self, key: &PluginServiceKey) -> Option<&PluginServiceStatus> {
        self.services.get(key)
    }

    pub fn mark_ready(&mut self, key: &PluginServiceKey, manifest_key: String) -> Result<()> {
        let status = self.services.get_mut(key).ok_or_else(|| {
            PluginError::Ensure(format!(
                "service {} is not registered",
                key.service_instance_id()
            ))
        })?;
        status.state = PluginServiceState::Ready;
        status.manifest_key = Some(manifest_key);
        status.last_error = None;
        Ok(())
    }

    pub fn mark_stale(&mut self, key: &PluginServiceKey, reason: impl Into<String>) -> Result<()> {
        let status = self.services.get_mut(key).ok_or_else(|| {
            PluginError::Ensure(format!(
                "service {} is not registered",
                key.service_instance_id()
            ))
        })?;
        status.state = PluginServiceState::Stale;
        status.last_error = Some(reason.into());
        Ok(())
    }

    pub fn statuses(&self) -> Vec<&PluginServiceStatus> {
        self.services.values().collect()
    }

    pub fn len(&self) -> usize {
        self.services.len()
    }

    pub fn is_empty(&self) -> bool {
        self.services.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::service::{RefreshStrategy, ServiceMode};

    fn key(profile: &str) -> PluginServiceKey {
        PluginServiceKey::new(
            "/eos/plugin/layer-stack",
            "/eos/plugin/workspace",
            "lsp",
            "digest-a",
            "pyright",
            profile,
            ServiceMode::WorkspaceSnapshotRefresh,
            RefreshStrategy::RemountWorkspaceAndNotify,
        )
        .expect("valid key")
    }

    #[test]
    fn registry_reuses_exact_key_only() {
        let mut registry = PluginServiceRegistry::new();
        registry.ensure(key("profile-a"));
        registry.ensure(key("profile-a"));
        registry.ensure(key("profile-b"));
        assert_eq!(registry.len(), 2);
    }

    #[test]
    fn ready_check_rejects_stale_manifest() {
        let mut registry = PluginServiceRegistry::new();
        let key = key("profile-a");
        registry.ensure(key.clone());
        registry
            .mark_ready(&key, "manifest@1".to_owned())
            .expect("mark ready");
        assert!(registry
            .get(&key)
            .expect("status")
            .require_ready_on_manifest("manifest@2")
            .is_err());
    }
}
