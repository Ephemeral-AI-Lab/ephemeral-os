//! Plugin service identity and refresh-policy contracts.
//!
//! These types are pure data. The daemon owns process lifecycle, namespace
//! remounts, PPC I/O, and snapshot leases; `eos-plugin` owns only the validated
//! key/strategy shapes shared by manifests, status, and tests.

use serde::{Deserialize, Serialize};

use crate::error::{PluginError, Result};

/// The daemon-managed service mode for long-lived read-only services.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ServiceMode {
    /// Read-only service refreshed against the latest LayerStack snapshot before
    /// each request.
    WorkspaceSnapshotRefresh,
    /// Stateless/write worker invoked through a fresh per-operation overlay.
    OneshotOverlay,
}

/// Mechanism used when a `workspace_snapshot_refresh` service is stale.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum RefreshStrategy {
    /// Remount the service workspace and send a daemon-level change
    /// notification to the harness.
    RemountWorkspaceAndNotify,
    /// Remount the service workspace; the service must reread on demand or
    /// invalidate its own caches.
    RemountWorkspace,
    /// Restart the service process on the new snapshot.
    RestartService,
}

/// Stable key for sharing a daemon-managed plugin service.
///
/// Reuse is intentionally stricter than the old warm-server cache: service id,
/// service profile digest, mode, and refresh strategy are part of the key so
/// two payloads cannot accidentally share a process just because they use the
/// same LayerStack root.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct PluginServiceKey {
    pub layer_stack_root: String,
    pub workspace_root: String,
    pub plugin_id: String,
    pub plugin_digest: String,
    pub service_id: String,
    pub service_profile_digest: String,
    pub service_mode: ServiceMode,
    pub refresh_strategy: RefreshStrategy,
}

impl PluginServiceKey {
    /// Construct a validated service key.
    pub fn new(
        layer_stack_root: impl Into<String>,
        workspace_root: impl Into<String>,
        plugin_id: impl Into<String>,
        plugin_digest: impl Into<String>,
        service_id: impl Into<String>,
        service_profile_digest: impl Into<String>,
        service_mode: ServiceMode,
        refresh_strategy: RefreshStrategy,
    ) -> Result<Self> {
        let key = Self {
            layer_stack_root: layer_stack_root.into(),
            workspace_root: workspace_root.into(),
            plugin_id: plugin_id.into(),
            plugin_digest: plugin_digest.into(),
            service_id: service_id.into(),
            service_profile_digest: service_profile_digest.into(),
            service_mode,
            refresh_strategy,
        };
        key.validate()?;
        Ok(key)
    }

    /// Validate this key without normalizing it.
    pub fn validate(&self) -> Result<()> {
        require_absolute("layer_stack_root", &self.layer_stack_root)?;
        require_absolute("workspace_root", &self.workspace_root)?;
        validate_identifier("plugin_id", &self.plugin_id)?;
        require_non_empty("plugin_digest", &self.plugin_digest)?;
        validate_identifier("service_id", &self.service_id)?;
        require_non_empty("service_profile_digest", &self.service_profile_digest)?;
        if self.service_mode == ServiceMode::OneshotOverlay
            && self.refresh_strategy != RefreshStrategy::RestartService
        {
            return Err(PluginError::Manifest(
                "oneshot_overlay service keys must use restart_service as the inert refresh strategy"
                    .to_owned(),
            ));
        }
        Ok(())
    }

    /// Manifest key used in status and refresh health checks.
    pub fn service_instance_id(&self) -> String {
        format!(
            "{}:{}:{}:{}",
            self.layer_stack_root, self.plugin_id, self.service_id, self.service_profile_digest
        )
    }
}

pub(crate) fn validate_identifier(field: &str, value: &str) -> Result<()> {
    let value = value.trim();
    if value.is_empty() {
        return Err(PluginError::Manifest(format!("{field} is required")));
    }
    let mut chars = value.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => {
            return Err(PluginError::Manifest(format!(
                "{field} must start with an ASCII letter or underscore"
            )));
        }
    }
    if chars.all(|c| c == '_' || c == '-' || c == '.' || c.is_ascii_alphanumeric()) {
        Ok(())
    } else {
        Err(PluginError::Manifest(format!(
            "{field} contains unsupported characters"
        )))
    }
}

pub(crate) fn require_non_empty(field: &str, value: &str) -> Result<()> {
    if value.trim().is_empty() {
        return Err(PluginError::Manifest(format!("{field} is required")));
    }
    Ok(())
}

fn require_absolute(field: &str, value: &str) -> Result<()> {
    require_non_empty(field, value)?;
    if value.starts_with('/') {
        Ok(())
    } else {
        Err(PluginError::Manifest(format!("{field} must be absolute")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn service_key_includes_profile_and_refresh_strategy() {
        let base = PluginServiceKey::new(
            "/eos/plugin/layer-stack",
            "/eos/plugin/workspace",
            "lsp",
            "digest-a",
            "pyright",
            "profile-a",
            ServiceMode::WorkspaceSnapshotRefresh,
            RefreshStrategy::RemountWorkspaceAndNotify,
        )
        .expect("valid service key");
        let mut changed = base.clone();
        changed.service_profile_digest = "profile-b".to_owned();
        assert_ne!(base, changed);

        let mut changed_strategy = base.clone();
        changed_strategy.refresh_strategy = RefreshStrategy::RestartService;
        assert_ne!(base, changed_strategy);
    }

    #[test]
    fn service_key_rejects_relative_workspace_paths() {
        assert!(matches!(
            PluginServiceKey::new(
                "relative",
                "/eos/plugin/workspace",
                "lsp",
                "digest-a",
                "pyright",
                "profile-a",
                ServiceMode::WorkspaceSnapshotRefresh,
                RefreshStrategy::RestartService,
            ),
            Err(PluginError::Manifest(message)) if message.contains("absolute")
        ));
    }
}
