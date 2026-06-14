//! Plugin service identity and refresh-policy contracts.
//!
//! These types are pure data. The daemon owns process lifecycle, namespace
//! remounts, PPC I/O, and snapshot leases; `plugin` owns only the validated
//! key/strategy shapes shared by manifests, status, and tests.

use serde::{Deserialize, Serialize};

use crate::error::{PluginError, Result};

/// The daemon-managed service mode for long-lived read-only services.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ServiceMode {
    /// Read-only service refreshed against the latest `LayerStack` snapshot before
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
/// Reuse is intentionally stricter than the old per-root cache: service id,
/// service profile digest, mode, and refresh strategy are part of the key so
/// two payloads cannot accidentally share a process just because they use the
/// same `LayerStack` root.
///
/// `#[non_exhaustive]` makes [`PluginServiceKey::new`] the only way to mint one
/// from another crate, so the validated `Parts` -> `new` path cannot be bypassed
/// by a struct literal; fields stay public for read-only access.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[non_exhaustive]
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

/// Field bag used to construct [`PluginServiceKey`] without a long positional
/// argument list.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginServiceKeyParts {
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
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Manifest`] when the supplied key parts are
    /// malformed or incompatible.
    pub fn new(parts: PluginServiceKeyParts) -> Result<Self> {
        let key = Self {
            layer_stack_root: parts.layer_stack_root,
            workspace_root: parts.workspace_root,
            plugin_id: parts.plugin_id,
            plugin_digest: parts.plugin_digest,
            service_id: parts.service_id,
            service_profile_digest: parts.service_profile_digest,
            service_mode: parts.service_mode,
            refresh_strategy: parts.refresh_strategy,
        };
        key.validate()?;
        Ok(key)
    }

    /// Validate this key without normalizing it.
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Manifest`] when paths are not absolute, identifiers
    /// are malformed, digests are empty, or the service mode/refresh strategy
    /// pair is invalid.
    pub fn validate(&self) -> Result<()> {
        require_absolute("layer_stack_root", &self.layer_stack_root)?;
        require_absolute("workspace_root", &self.workspace_root)?;
        validate_plugin_id("plugin_id", &self.plugin_id)?;
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

    /// Deterministic, content-addressed reuse key for a service instance.
    ///
    /// Despite the `_id` suffix this is NOT a per-spawn unique handle: it is a
    /// stable digest of the identity dimensions that define instance sharing
    /// (layer-stack root, plugin, service, profile digest), so two payloads with
    /// the same dimensions intentionally resolve to — and reuse — one instance.
    /// Used as the manifest key in status and refresh health checks.
    #[must_use]
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

pub(crate) fn validate_plugin_id(field: &str, value: &str) -> Result<()> {
    let value = value.trim();
    if value.is_empty() {
        return Err(PluginError::Manifest(format!("{field} is required")));
    }
    if is_valid_plugin_name(value) {
        Ok(())
    } else {
        Err(PluginError::Manifest(format!(
            "{field} must match ^[A-Za-z_][A-Za-z0-9_]*$"
        )))
    }
}

/// Whether `name` matches the Rust `_PLUGIN_NAME_RE` (`^[A-Za-z_][A-Za-z0-9_]*$`).
fn is_valid_plugin_name(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => return false,
    }
    chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
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
#[path = "../tests/unit/service.rs"]
mod tests;
