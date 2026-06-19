//! Daemon-owned services shared by dispatch handlers.

use std::time::Duration;

use command::CommandConfig;
use config::configs::daemon::{CommandConfig as ConfigCommandConfig, PluginRuntimeConfig};
use config::configs::isolated::IsolatedNetworkConfig;
use layerstack::service::{BoundedCaptureOptions, IgnoredCaptureLimits};
use layerstack::CommitOptions;

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
    pub commit_options: CommitOptions,
}

impl RuntimeServices {
    #[must_use]
    pub fn new(
        plugin: PluginRuntimeConfig,
        isolated: IsolatedNetworkConfig,
        command: CommandConfig,
    ) -> Self {
        Self::with_commit_options_and_capture_options(
            plugin,
            isolated,
            command,
            CommitOptions::default(),
            BoundedCaptureOptions::default(),
        )
    }

    #[must_use]
    pub fn with_commit_options_and_capture_options(
        _plugin: PluginRuntimeConfig,
        _isolated: IsolatedNetworkConfig,
        _command: CommandConfig,
        commit_options: CommitOptions,
        _capture_options: BoundedCaptureOptions,
    ) -> Self {
        Self {
            commit_options,
        }
    }
}
