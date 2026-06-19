//! Typed schema for the daemon section of `eos-sandbox/config/prd.yml`.
//!
//! The `eosd` binary loads this section from the merged runtime YAML and injects
//! it into daemon-owned subsystems during server startup.

use std::path::PathBuf;

use serde::Deserialize;

use crate::configs::validate::{
    require_absolute, require_f64_gt, require_timestamp_timezone, require_u64_at_least,
    require_usize_at_least, ConfigFieldError,
};

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DaemonConfig {
    pub server: DaemonServerConfig,
    pub inflight: InflightConfig,
    pub commands: CommandConfig,
    pub idle_workspace_eviction: IdleWorkspaceEvictionConfig,
    pub layer_stack: LayerStackConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CommandConfig {
    pub scratch_root: PathBuf,
    pub default_yield_time_ms: u64,
    pub default_timeout_s: u64,
    pub quiet_ms: u64,
    pub cancel_wait_ms: u64,
    pub output_drain_grace_ms: u64,
    pub max_command_s: u64,
    pub transcript_timestamp_timezone: String,
    pub ignored_capture: IgnoredCaptureConfig,
}

impl Default for CommandConfig {
    fn default() -> Self {
        Self {
            scratch_root: PathBuf::from("/eos/scratch/commands"),
            default_yield_time_ms: 1000,
            default_timeout_s: 600,
            quiet_ms: 50,
            cancel_wait_ms: 500,
            output_drain_grace_ms: 500,
            max_command_s: 6 * 60 * 60,
            transcript_timestamp_timezone: "UTC".to_owned(),
            ignored_capture: IgnoredCaptureConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IgnoredCaptureConfig {
    pub max_files: usize,
    pub max_bytes: u64,
    pub max_file_bytes: u64,
    pub spool_threshold_bytes: u64,
    pub max_metadata_capture_duration_ms: u64,
}

impl Default for IgnoredCaptureConfig {
    fn default() -> Self {
        Self {
            max_files: 4096,
            max_bytes: 64 * 1024 * 1024,
            max_file_bytes: 16 * 1024 * 1024,
            spool_threshold_bytes: 1024 * 1024,
            max_metadata_capture_duration_ms: 30_000,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DaemonServerConfig {
    pub socket_path: PathBuf,
    pub pid_path: PathBuf,
    pub max_worker_threads: usize,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InflightConfig {
    pub ttl_s: f64,
    pub reaper_interval_s: f64,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdleWorkspaceEvictionConfig {
    pub interval_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LayerStackConfig {
    pub auto_squash_max_depth: usize,
}

impl DaemonConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates daemon runtime policy.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        require_absolute(&self.server.socket_path, "daemon.server.socket_path")?;
        require_absolute(&self.server.pid_path, "daemon.server.pid_path")?;
        require_usize_at_least(
            self.server.max_worker_threads,
            1,
            "daemon.server.max_worker_threads",
        )?;
        require_f64_gt(self.inflight.ttl_s, 0.0, "daemon.inflight.ttl_s")?;
        require_f64_gt(
            self.inflight.reaper_interval_s,
            0.0,
            "daemon.inflight.reaper_interval_s",
        )?;
        require_absolute(&self.commands.scratch_root, "daemon.commands.scratch_root")?;
        reject_dangerous_command_scratch_root(&self.commands.scratch_root)?;
        require_u64_at_least(
            self.commands.default_yield_time_ms,
            1,
            "daemon.commands.default_yield_time_ms",
        )?;
        require_u64_at_least(
            self.commands.default_timeout_s,
            1,
            "daemon.commands.default_timeout_s",
        )?;
        require_u64_at_least(self.commands.quiet_ms, 1, "daemon.commands.quiet_ms")?;
        require_u64_at_least(
            self.commands.cancel_wait_ms,
            1,
            "daemon.commands.cancel_wait_ms",
        )?;
        require_u64_at_least(
            self.commands.output_drain_grace_ms,
            1,
            "daemon.commands.output_drain_grace_ms",
        )?;
        require_u64_at_least(
            self.commands.max_command_s,
            1,
            "daemon.commands.max_command_s",
        )?;
        require_timestamp_timezone(
            &self.commands.transcript_timestamp_timezone,
            "daemon.commands.transcript_timestamp_timezone",
        )?;
        validate_ignored_capture_limits(&self.commands.ignored_capture)?;
        require_u64_at_least(
            self.idle_workspace_eviction.interval_ms,
            1,
            "daemon.idle_workspace_eviction.interval_ms",
        )?;
        require_usize_at_least(
            self.layer_stack.auto_squash_max_depth,
            1,
            "daemon.layer_stack.auto_squash_max_depth",
        )?;
        Ok(())
    }
}

fn validate_ignored_capture_limits(limits: &IgnoredCaptureConfig) -> Result<(), ConfigFieldError> {
    require_usize_at_least(
        limits.max_files,
        1,
        "daemon.commands.ignored_capture.max_files",
    )?;
    require_u64_at_least(
        limits.max_bytes,
        1,
        "daemon.commands.ignored_capture.max_bytes",
    )?;
    require_u64_at_least(
        limits.max_file_bytes,
        1,
        "daemon.commands.ignored_capture.max_file_bytes",
    )?;
    require_u64_at_least(
        limits.spool_threshold_bytes,
        1,
        "daemon.commands.ignored_capture.spool_threshold_bytes",
    )?;
    require_u64_at_least(
        limits.max_metadata_capture_duration_ms,
        1,
        "daemon.commands.ignored_capture.max_metadata_capture_duration_ms",
    )?;
    if limits.max_file_bytes > limits.max_bytes {
        return Err(ConfigFieldError::new(
            "daemon.commands.ignored_capture.max_file_bytes",
            "must be at most daemon.commands.ignored_capture.max_bytes",
        ));
    }
    if limits.spool_threshold_bytes >= limits.max_bytes {
        return Err(ConfigFieldError::new(
            "daemon.commands.ignored_capture.spool_threshold_bytes",
            "must be less than daemon.commands.ignored_capture.max_bytes",
        ));
    }
    Ok(())
}

fn reject_dangerous_command_scratch_root(
    scratch_root: &std::path::Path,
) -> Result<(), ConfigFieldError> {
    if is_filesystem_root(scratch_root) {
        return Err(ConfigFieldError::new(
            "daemon.commands.scratch_root",
            "must not be the filesystem root",
        ));
    }
    Ok(())
}

fn is_filesystem_root(path: &std::path::Path) -> bool {
    path.parent().is_none()
        || path
            .canonicalize()
            .ok()
            .is_some_and(|canonical| canonical.parent().is_none())
}
