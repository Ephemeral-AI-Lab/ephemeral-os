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
    #[must_use]
    pub const fn new(key: PluginServiceKey) -> Self {
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
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::ProjectionStale`] when the service is not ready or
    /// is ready for a different manifest key.
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
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Get or insert a service status.
    pub fn ensure(&mut self, key: PluginServiceKey) -> &mut PluginServiceStatus {
        self.services
            .entry(key.clone())
            .or_insert_with(|| PluginServiceStatus::new(key))
    }

    #[must_use]
    pub fn get(&self, key: &PluginServiceKey) -> Option<&PluginServiceStatus> {
        self.services.get(key)
    }

    /// Mark a registered service ready for `manifest_key`.
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Ensure`] when `key` has not been registered.
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

    #[must_use]
    pub fn statuses(&self) -> Vec<&PluginServiceStatus> {
        self.services.values().collect()
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.services.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.services.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::service::PluginServiceKeyParts;
    use crate::service::{RefreshStrategy, ServiceMode};

    type TestResult = std::result::Result<(), PluginError>;

    fn key(profile: &str) -> Result<PluginServiceKey> {
        PluginServiceKey::new(PluginServiceKeyParts {
            layer_stack_root: "/eos/plugin/layer-stack".to_owned(),
            workspace_root: "/eos/plugin/workspace".to_owned(),
            plugin_id: "generic".to_owned(),
            plugin_digest: "digest-a".to_owned(),
            service_id: "worker".to_owned(),
            service_profile_digest: profile.to_owned(),
            service_mode: ServiceMode::WorkspaceSnapshotRefresh,
            refresh_strategy: RefreshStrategy::RemountWorkspaceAndNotify,
        })
    }

    #[test]
    fn registry_reuses_exact_key_only() -> TestResult {
        let mut registry = PluginServiceRegistry::new();
        registry.ensure(key("profile-a")?);
        registry.ensure(key("profile-a")?);
        registry.ensure(key("profile-b")?);
        assert_eq!(registry.len(), 2);
        Ok(())
    }

    #[test]
    fn ready_check_rejects_stale_manifest() -> TestResult {
        let mut registry = PluginServiceRegistry::new();
        let key = key("profile-a")?;
        registry.ensure(key.clone());
        registry.mark_ready(&key, "manifest@1".to_owned())?;
        let Some(status) = registry.get(&key) else {
            return Err(PluginError::Ensure(
                "expected service status after ensure".to_owned(),
            ));
        };
        assert!(status.require_ready_on_manifest("manifest@2").is_err());
        Ok(())
    }
}
