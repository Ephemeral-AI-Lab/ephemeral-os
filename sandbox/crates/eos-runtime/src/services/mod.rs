//! Host-neutral runtime service composition.

#![forbid(unsafe_code)]

use std::sync::Arc;

use eos_config::configs::daemon::PluginRuntimeConfig;
use eos_config::configs::isolated_workspace::IsolatedWorkspaceConfig;
use eos_isolated_workspace::NsRunnerLauncher;
use eos_plugin::PluginError;
use serde_json::Value;

use crate::plugin::ensure::validate_plugin_caller_fields;
use crate::plugin::{PluginRuntime, PluginRuntimeError};
use crate::workspace::WorkspaceRuntime;

/// Runtime service instances shared by daemon dispatch handlers.
pub struct RuntimeServices {
    pub plugin: PluginRuntime,
    pub workspace: WorkspaceRuntime,
}

impl RuntimeServices {
    /// Build runtime services from typed config sections and a namespace-runner
    /// launcher supplied by the embedding daemon.
    #[must_use]
    pub fn new(
        plugin: PluginRuntimeConfig,
        isolated_workspace: IsolatedWorkspaceConfig,
        launcher: Arc<dyn NsRunnerLauncher>,
    ) -> Self {
        Self {
            plugin: PluginRuntime::new(plugin, launcher),
            workspace: WorkspaceRuntime::new(isolated_workspace),
        }
    }

    /// Validate the plugin caller fields and reject plugin calls from callers
    /// that currently own an isolated workspace.
    ///
    /// # Errors
    ///
    /// Returns [`PluginRuntimeError`] when caller metadata is invalid or the
    /// caller is isolated.
    pub fn ensure_plugin_family_allowed(&self, args: &Value) -> Result<(), PluginRuntimeError> {
        validate_plugin_caller_fields(args)?;
        let caller_id = args
            .get("caller_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        if !caller_id.is_empty() && self.workspace.caller_has_active_handle(caller_id) {
            return Err(PluginRuntimeError::Plugin(
                PluginError::ForbiddenInIsolatedWorkspace,
            ));
        }
        Ok(())
    }
}
