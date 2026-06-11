//! Plugin manifest contracts.

use std::collections::{BTreeMap, BTreeSet};

use eos_namespace::protocol::Intent;
use serde::{Deserialize, Serialize};

use crate::error::{PluginError, Result};
use crate::service::{
    require_non_empty, validate_identifier, validate_plugin_id, RefreshStrategy, ServiceMode,
};

/// Digest marker written after a package tree is accepted.
pub const PACKAGE_SHA256_MARKER: &str = ".package-sha256";

/// Digest marker written after setup completes successfully.
pub const SETUP_SHA256_MARKER: &str = ".setup-sha256";

/// Top-level plugin manifest consumed by `sandbox.plugin.ensure`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginManifest {
    pub plugin_id: String,
    pub plugin_version: String,
    pub plugin_digest: String,
    #[serde(default)]
    pub package: PluginPackageManifest,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub setup: Option<PluginSetupManifest>,
    #[serde(default)]
    pub services: Vec<PluginServiceManifest>,
    #[serde(default)]
    pub operations: Vec<PluginOperationManifest>,
}

impl PluginManifest {
    /// Validate manifest identity, service uniqueness, and operation references.
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Manifest`] when identifiers are malformed, required
    /// fields are empty, services or operations are duplicated, or an operation
    /// references an unknown service.
    pub fn validate(&self) -> Result<()> {
        validate_plugin_id("plugin_id", &self.plugin_id)?;
        require_non_empty("plugin_version", &self.plugin_version)?;
        require_non_empty("plugin_digest", &self.plugin_digest)?;
        self.package.validate()?;
        if let Some(setup) = &self.setup {
            setup.validate()?;
        }

        let mut service_ids = BTreeSet::new();
        let mut service_modes = BTreeMap::new();
        for service in &self.services {
            service.validate()?;
            if !service_ids.insert(service.service_id.as_str()) {
                return Err(PluginError::Manifest(format!(
                    "duplicate service_id {}",
                    service.service_id
                )));
            }
            service_modes.insert(service.service_id.as_str(), service.service_mode);
        }

        let mut op_names = BTreeSet::new();
        for operation in &self.operations {
            operation.validate(&service_modes)?;
            if !op_names.insert(operation.op_name.as_str()) {
                return Err(PluginError::Manifest(format!(
                    "duplicate op_name {}",
                    operation.op_name
                )));
            }
        }
        Ok(())
    }

    /// Digest recorded in [`PACKAGE_SHA256_MARKER`] for package idempotency.
    #[must_use]
    pub fn package_marker_digest(&self) -> &str {
        &self.plugin_digest
    }

    /// Digest recorded in [`SETUP_SHA256_MARKER`] after setup succeeds.
    #[must_use]
    pub fn setup_marker_digest(&self) -> Option<&str> {
        self.setup
            .as_ref()
            .map(|setup| setup.setup_marker_digest.as_str())
    }
}

/// Package-root contract for an installed plugin payload.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginPackageManifest {
    #[serde(default = "default_runtime_dir")]
    pub runtime_dir: String,
    #[serde(default)]
    pub dependency_scope: PluginDependencyScope,
}

impl Default for PluginPackageManifest {
    fn default() -> Self {
        Self {
            runtime_dir: default_runtime_dir(),
            dependency_scope: PluginDependencyScope::PackageDigest,
        }
    }
}

impl PluginPackageManifest {
    fn validate(&self) -> Result<()> {
        validate_relative_package_path("package.runtime_dir", &self.runtime_dir)
    }
}

/// Dependency isolation scope for package-managed runtime dependencies.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum PluginDependencyScope {
    /// Dependencies live under `/eos/runtime/packages/<plugin>/<digest>/`.
    #[default]
    PackageDigest,
}

/// Optional setup command executed by the daemon after package publish.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginSetupManifest {
    pub command: Vec<String>,
    pub working_dir: String,
    pub setup_marker_digest: String,
    pub timeout_ms: u64,
}

impl PluginSetupManifest {
    fn validate(&self) -> Result<()> {
        if self.command.is_empty() {
            return Err(PluginError::Manifest(
                "setup.command must not be empty".to_owned(),
            ));
        }
        validate_relative_package_path("setup.working_dir", &self.working_dir)?;
        require_non_empty("setup_marker_digest", &self.setup_marker_digest)?;
        if self.timeout_ms == 0 {
            return Err(PluginError::Manifest(
                "setup.timeout_ms must be positive".to_owned(),
            ));
        }
        Ok(())
    }
}

/// One service declared by a plugin payload.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginServiceManifest {
    pub service_id: String,
    pub service_profile_digest: String,
    pub service_mode: ServiceMode,
    pub refresh_strategy: RefreshStrategy,
    #[serde(default)]
    pub command: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub working_dir: Option<String>,
    #[serde(default = "default_ppc_protocol")]
    pub ppc_protocol_version: u32,
}

impl PluginServiceManifest {
    /// Validate this service declaration.
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Manifest`] when the service identity/profile is
    /// invalid, the PPC protocol is zero, or an executable service mode lacks a
    /// launch command.
    pub fn validate(&self) -> Result<()> {
        validate_identifier("service_id", &self.service_id)?;
        require_non_empty("service_profile_digest", &self.service_profile_digest)?;
        if self.ppc_protocol_version == 0 {
            return Err(PluginError::Manifest(
                "ppc_protocol_version must be positive".to_owned(),
            ));
        }
        if let Some(working_dir) = &self.working_dir {
            validate_relative_package_path("service.working_dir", working_dir)?;
        }
        if matches!(
            self.service_mode,
            ServiceMode::WorkspaceSnapshotRefresh | ServiceMode::OneshotOverlay
        ) && self.command.is_empty()
        {
            return Err(PluginError::Manifest(format!(
                "service {} requires a launch command",
                self.service_id
            )));
        }
        Ok(())
    }
}

/// One public `plugin.<plugin>.<op>` operation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginOperationManifest {
    pub op_name: String,
    pub intent: Intent,
    #[serde(default = "default_auto_workspace_overlay")]
    pub auto_workspace_overlay: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub service_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_ms: Option<u64>,
}

impl PluginOperationManifest {
    fn validate(&self, service_modes: &BTreeMap<&str, ServiceMode>) -> Result<()> {
        require_non_empty("op_name", &self.op_name)?;
        if self.intent == Intent::Lifecycle {
            return Err(PluginError::Manifest(
                "Intent::Lifecycle is reserved for sandbox lifecycle ops".to_owned(),
            ));
        }
        if self.intent == Intent::ReadOnly && self.service_id.is_none() {
            return Err(PluginError::Manifest(format!(
                "read-only op {} must reference a service_id",
                self.op_name
            )));
        }
        if let Some(service_id) = &self.service_id {
            validate_identifier("service_id", service_id)?;
            if !service_modes.contains_key(service_id.as_str()) {
                return Err(PluginError::Manifest(format!(
                    "op {} references unknown service_id {}",
                    self.op_name, service_id
                )));
            }
        }
        if self.intent == Intent::WriteAllowed && self.auto_workspace_overlay {
            let Some(service_id) = &self.service_id else {
                return Err(PluginError::Manifest(format!(
                    "write op {} with auto_workspace_overlay requires an oneshot_overlay service",
                    self.op_name
                )));
            };
            if service_modes.get(service_id.as_str()) != Some(&ServiceMode::OneshotOverlay) {
                return Err(PluginError::Manifest(format!(
                    "write op {} with auto_workspace_overlay requires an oneshot_overlay service",
                    self.op_name
                )));
            }
        }
        if self.timeout_ms == Some(0) {
            return Err(PluginError::Manifest(format!(
                "op {} timeout_ms must be positive",
                self.op_name
            )));
        }
        Ok(())
    }
}

const fn default_ppc_protocol() -> u32 {
    1
}

const fn default_auto_workspace_overlay() -> bool {
    true
}

fn default_runtime_dir() -> String {
    "runtime".to_owned()
}

fn validate_relative_package_path(field: &str, value: &str) -> Result<()> {
    require_non_empty(field, value)?;
    if value.starts_with('/') {
        return Err(PluginError::Manifest(format!("{field} must be relative")));
    }
    for component in value.split('/') {
        if component == ".." {
            return Err(PluginError::Manifest(format!(
                "{field} must not contain path traversal"
            )));
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "../tests/unit/manifest.rs"]
mod tests;
