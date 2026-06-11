//! Daemon-owned runtime services shared by dispatch handlers.

use std::sync::Arc;

use eos_config::configs::daemon::PluginRuntimeConfig;
use eos_config::configs::isolated_workspace::IsolatedWorkspaceConfig;
use eos_operation::plugin::{PluginRuntime, PluginRuntimeError};
use eos_plugin::PluginError;
use serde_json::Value;

use crate::WorkspaceRuntime;

pub(crate) mod sweepers {
    use crate::WorkspaceRuntime;

    #[must_use]
    pub(crate) fn sweep_workspace_ttl(workspace: &WorkspaceRuntime) -> usize {
        workspace.ttl_sweep()
    }

    pub(crate) fn sweep_command_sessions() {
        eos_operation::command::runtime::command_session_reaper_sweep();
    }

    pub(crate) fn recover_orphaned_command_sessions() {
        eos_operation::command::runtime::recover_orphaned_command_sessions();
    }
}

/// Runtime service instances shared by daemon dispatch handlers.
pub struct RuntimeServices {
    pub plugin: PluginRuntime,
    pub workspace: WorkspaceRuntime,
}

impl RuntimeServices {
    #[must_use]
    pub fn new(
        plugin: PluginRuntimeConfig,
        isolated_workspace: IsolatedWorkspaceConfig,
        launcher: Arc<dyn eos_workspace::NsRunnerLauncher>,
    ) -> Self {
        Self {
            plugin: PluginRuntime::new(plugin, launcher),
            workspace: WorkspaceRuntime::new(isolated_workspace),
        }
    }

    pub fn ensure_plugin_family_allowed(&self, args: &Value) -> Result<(), PluginRuntimeError> {
        eos_operation::plugin::ensure::validate_plugin_caller_fields(args)?;
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
