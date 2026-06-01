//! Plugin manifest contracts.

use std::collections::BTreeSet;

use eos_protocol::Intent;
use serde::{Deserialize, Serialize};

use crate::error::{PluginError, Result};
use crate::service::{require_non_empty, validate_identifier, RefreshStrategy, ServiceMode};

/// Top-level plugin manifest consumed by `api.plugin.ensure`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginManifest {
    pub plugin_id: String,
    pub plugin_version: String,
    pub plugin_digest: String,
    #[serde(default)]
    pub services: Vec<PluginServiceManifest>,
    #[serde(default)]
    pub operations: Vec<PluginOperationManifest>,
}

impl PluginManifest {
    /// Validate manifest identity, service uniqueness, and operation references.
    pub fn validate(&self) -> Result<()> {
        validate_identifier("plugin_id", &self.plugin_id)?;
        require_non_empty("plugin_version", &self.plugin_version)?;
        require_non_empty("plugin_digest", &self.plugin_digest)?;

        let mut service_ids = BTreeSet::new();
        for service in &self.services {
            service.validate()?;
            if !service_ids.insert(service.service_id.as_str()) {
                return Err(PluginError::Manifest(format!(
                    "duplicate service_id {}",
                    service.service_id
                )));
            }
        }

        let mut op_names = BTreeSet::new();
        for operation in &self.operations {
            operation.validate(&service_ids)?;
            if !op_names.insert(operation.op_name.as_str()) {
                return Err(PluginError::Manifest(format!(
                    "duplicate op_name {}",
                    operation.op_name
                )));
            }
        }
        Ok(())
    }
}

/// One service declared by a plugin payload.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PluginServiceManifest {
    pub service_id: String,
    pub service_profile_digest: String,
    pub service_mode: ServiceMode,
    pub refresh_strategy: RefreshStrategy,
    #[serde(default)]
    pub command: Vec<String>,
    #[serde(default = "default_ppc_protocol")]
    pub ppc_protocol_version: u32,
}

impl PluginServiceManifest {
    pub fn validate(&self) -> Result<()> {
        validate_identifier("service_id", &self.service_id)?;
        require_non_empty("service_profile_digest", &self.service_profile_digest)?;
        if self.ppc_protocol_version == 0 {
            return Err(PluginError::Manifest(
                "ppc_protocol_version must be positive".to_owned(),
            ));
        }
        if self.service_mode == ServiceMode::WorkspaceSnapshotRefresh && self.command.is_empty() {
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
    fn validate(&self, service_ids: &BTreeSet<&str>) -> Result<()> {
        validate_identifier("op_name", &self.op_name)?;
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
            if !service_ids.contains(service_id.as_str()) {
                return Err(PluginError::Manifest(format!(
                    "op {} references unknown service_id {}",
                    self.op_name, service_id
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

fn default_ppc_protocol() -> u32 {
    1
}

fn default_auto_workspace_overlay() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manifest() -> PluginManifest {
        PluginManifest {
            plugin_id: "lsp".to_owned(),
            plugin_version: "0.1.0".to_owned(),
            plugin_digest: "digest-a".to_owned(),
            services: vec![PluginServiceManifest {
                service_id: "pyright".to_owned(),
                service_profile_digest: "profile-a".to_owned(),
                service_mode: ServiceMode::WorkspaceSnapshotRefresh,
                refresh_strategy: RefreshStrategy::RemountWorkspaceAndNotify,
                command: vec!["pyright-langserver".to_owned(), "--stdio".to_owned()],
                ppc_protocol_version: 1,
            }],
            operations: vec![PluginOperationManifest {
                op_name: "hover".to_owned(),
                intent: Intent::ReadOnly,
                auto_workspace_overlay: true,
                service_id: Some("pyright".to_owned()),
                timeout_ms: Some(5_000),
            }],
        }
    }

    #[test]
    fn validates_read_only_service_manifest() {
        manifest().validate().expect("manifest is valid");
    }

    #[test]
    fn rejects_read_only_op_without_service() {
        let mut manifest = manifest();
        manifest.operations[0].service_id = None;
        assert!(matches!(
            manifest.validate(),
            Err(PluginError::Manifest(message)) if message.contains("must reference")
        ));
    }

    #[test]
    fn rejects_duplicate_operation_names() {
        let mut manifest = manifest();
        manifest.operations.push(manifest.operations[0].clone());
        assert!(matches!(
            manifest.validate(),
            Err(PluginError::Manifest(message)) if message.contains("duplicate op_name")
        ));
    }
}
