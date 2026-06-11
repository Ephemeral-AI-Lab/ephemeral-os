//! The daemon's owned service instances.
//!
//! `DaemonServer` constructs one [`Services`] from typed config and threads a
//! shared reference through [`crate::runtime::context::DispatchContext`]. This
//! is the explicit replacement for process-global service state: handlers reach
//! the plugin and isolated-workspace runtimes only through the context, and
//! nothing else may be added here.

use std::sync::Arc;

use eos_config::configs::daemon::PluginRuntimeConfig;
use eos_config::configs::isolated_workspace::IsolatedWorkspaceConfig;
use eos_workspace_runtime::WorkspaceRuntime;

use crate::runtime::ns_runner::{DaemonNsRunnerLauncher, NsRunnerLauncher};
use crate::services::plugin::PluginRuntime;

/// Per-server daemon services used by dispatch handlers.
pub struct Services {
    pub(crate) plugin: PluginRuntime,
    pub(crate) workspace: WorkspaceRuntime,
}

impl Services {
    /// Build the daemon services from their typed config sections, launching
    /// ns-runner children through the daemon's own binary.
    #[must_use]
    pub fn new(plugin: PluginRuntimeConfig, isolated_workspace: IsolatedWorkspaceConfig) -> Self {
        Self::with_ns_runner_launcher(
            plugin,
            isolated_workspace,
            Arc::new(DaemonNsRunnerLauncher::default()),
        )
    }

    /// Crate-local constructor seam: tests inject a fake launcher here.
    pub(crate) fn with_ns_runner_launcher(
        plugin: PluginRuntimeConfig,
        isolated_workspace: IsolatedWorkspaceConfig,
        launcher: Arc<dyn NsRunnerLauncher>,
    ) -> Self {
        Self {
            plugin: PluginRuntime::new(plugin, launcher),
            workspace: WorkspaceRuntime::new(isolated_workspace),
        }
    }
}

impl Default for Services {
    fn default() -> Self {
        Self::new(
            PluginRuntimeConfig::default(),
            IsolatedWorkspaceConfig::default(),
        )
    }
}
