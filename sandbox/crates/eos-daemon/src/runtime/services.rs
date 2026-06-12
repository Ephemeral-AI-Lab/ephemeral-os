//! Daemon-owned runtime services shared by dispatch handlers.

use std::sync::Arc;

use eos_config::configs::daemon::PluginRuntimeConfig;
use eos_config::configs::isolated_workspace::IsolatedWorkspaceConfig;
use eos_operation::plugin::{PluginRuntime, PluginRuntimeError};
use eos_operation::CallerId;
use eos_plugin::PluginError;
use serde_json::Value;

use crate::WorkspaceRuntime;

pub(crate) mod background_tasks {
    use crate::WorkspaceRuntime;

    #[must_use]
    pub(crate) fn evict_idle_workspaces_once(workspace: &WorkspaceRuntime) -> usize {
        let report = workspace.evict_idle_workspaces_report();
        let count = report.evicted.len();
        if count > 0 {
            crate::trace::push_background_record(crate::trace::idle_workspace_evict_record(
                &report,
            ));
        }
        count
    }

    pub(crate) fn advance_active_commands_once() {
        for record in eos_operation::command::runtime::advance_active_commands_once() {
            crate::trace::push_background_record(record);
        }
    }

    pub(crate) fn recover_orphaned_commands() {
        eos_operation::command::runtime::recover_orphaned_commands();
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
        self.ensure_plugin_caller_allowed(&CallerId::from_wire(args))
    }

    pub fn ensure_plugin_caller_allowed(
        &self,
        caller: &CallerId,
    ) -> Result<(), PluginRuntimeError> {
        if !caller.as_str().is_empty() && self.workspace.caller_has_active_handle(caller.as_str()) {
            return Err(PluginRuntimeError::Plugin(
                PluginError::ForbiddenInIsolatedWorkspace,
            ));
        }
        Ok(())
    }
}
