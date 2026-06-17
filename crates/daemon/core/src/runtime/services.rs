//! Daemon-owned runtime services shared by dispatch handlers.

use std::sync::Arc;
use std::time::Duration;

use command::CommandConfig;
use config::configs::daemon::{CommandConfig as ConfigCommandConfig, PluginRuntimeConfig};
use config::configs::isolated_network::IsolatedNetworkConfig;
use layerstack::service::{BoundedCaptureOptions, IgnoredCaptureLimits};
use layerstack::CommitOptions;
use operation::command::CommandOps;
use plugin::{PluginRuntime, PluginRuntimeError};
use serde_json::Value;

use crate::WorkspaceRuntime;

pub(crate) mod background_tasks {
    use operation::command::CommandOps;

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

    pub(crate) fn advance_active_commands_once(command: &CommandOps) {
        for record in command.advance_active_commands_once(std::time::Instant::now()) {
            crate::trace::push_background_record(record);
        }
    }

    pub(crate) fn recover_orphaned_commands(command: &CommandOps) {
        command.recover_orphaned_commands();
    }
}

#[must_use]
pub(crate) fn command_config_from_schema(config: &ConfigCommandConfig) -> CommandConfig {
    CommandConfig {
        scratch_root: config.scratch_root.clone(),
        default_yield_time_ms: config.default_yield_time_ms,
        default_timeout_s: config.default_timeout_s,
        quiet_ms: config.quiet_ms,
        cancel_wait_ms: config.cancel_wait_ms,
        output_drain_grace_ms: config.output_drain_grace_ms,
        max_command_s: config.max_command_s,
        transcript_timestamp_timezone: config.transcript_timestamp_timezone.clone(),
    }
}

#[must_use]
pub(crate) fn capture_options_from_schema(config: &ConfigCommandConfig) -> BoundedCaptureOptions {
    let limits = config.ignored_capture;
    BoundedCaptureOptions {
        materialize_payloads: true,
        ignored_limits: IgnoredCaptureLimits {
            max_ignored_files: limits.max_files,
            max_ignored_bytes: limits.max_bytes,
            max_ignored_file_bytes: limits.max_file_bytes,
            spool_threshold_bytes: limits.spool_threshold_bytes,
            max_metadata_capture_duration: Duration::from_millis(
                limits.max_metadata_capture_duration_ms,
            ),
        },
    }
}

/// Runtime service instances shared by daemon dispatch handlers.
pub struct RuntimeServices {
    pub command: Arc<CommandOps>,
    pub commit_options: CommitOptions,
    pub plugin: PluginRuntime,
    pub workspace: WorkspaceRuntime,
}

impl RuntimeServices {
    #[must_use]
    pub fn new(
        plugin: PluginRuntimeConfig,
        isolated_network: IsolatedNetworkConfig,
        command: CommandConfig,
    ) -> Self {
        Self::with_commit_options_and_capture_options(
            plugin,
            isolated_network,
            command,
            CommitOptions::default(),
            BoundedCaptureOptions::default(),
        )
    }

    #[must_use]
    pub fn with_commit_options_and_capture_options(
        plugin: PluginRuntimeConfig,
        isolated_network: IsolatedNetworkConfig,
        command: CommandConfig,
        commit_options: CommitOptions,
        capture_options: BoundedCaptureOptions,
    ) -> Self {
        let command = Arc::new(CommandOps::with_commit_options_and_capture_options(
            command,
            commit_options,
            capture_options,
        ));
        Self {
            command: Arc::clone(&command),
            commit_options,
            plugin: PluginRuntime::new(plugin),
            workspace: WorkspaceRuntime::new(isolated_network, command),
        }
    }

    pub fn ensure_plugin_family_allowed(&self, args: &Value) -> Result<(), PluginRuntimeError> {
        plugin_contract::validate_plugin_caller_fields(args)
            .map_err(|err| PluginRuntimeError::InvalidRequest(err.message()))?;
        self.ensure_plugin_caller_allowed(plugin_contract::CallerId::from_wire(args).as_str())
    }

    pub fn ensure_plugin_caller_allowed(&self, caller: &str) -> Result<(), PluginRuntimeError> {
        if !caller.is_empty() && self.workspace.caller_has_active_handle(caller) {
            return Err(PluginRuntimeError::ForbiddenInIsolatedNetwork);
        }
        Ok(())
    }
}
