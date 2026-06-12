//! Typed schema for the daemon section of `sandbox/config/prd.yml`.
//!
//! The `eosd` binary loads this section from the merged runtime YAML and injects
//! it into daemon-owned subsystems during server startup.

use std::path::PathBuf;

use serde::Deserialize;

use crate::configs::validate::{
    require_absolute, require_f64_gt, require_timestamp_timezone, require_u64_at_least,
    require_usize_at_least, ConfigFieldError,
};

pub use super::command_session::CommandConfig;

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DaemonConfig {
    pub server: DaemonServerConfig,
    pub inflight: InflightConfig,
    pub command_sessions: CommandConfig,
    pub isolated_sweeper: IsolatedSweeperConfig,
    pub plugin: PluginRuntimeConfig,
    pub layer_stack: LayerStackConfig,
    pub files: FileLimitsConfig,
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
pub struct IsolatedSweeperConfig {
    pub ttl_sweep_interval_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginRuntimeConfig {
    pub ppc_root: PathBuf,
    pub ppc_timeout_ms: u64,
    pub service_probe_timeout_ms: u64,
    pub max_response_bytes: usize,
}

impl Default for PluginRuntimeConfig {
    /// Production fallbacks used when no `daemon.plugin` section is injected
    /// (matches `sandbox/config/prd.yml`).
    fn default() -> Self {
        Self {
            ppc_root: PathBuf::from("/eos/plugin/ppc"),
            ppc_timeout_ms: 5_000,
            service_probe_timeout_ms: 5_000,
            max_response_bytes: 8 * 1024 * 1024,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LayerStackConfig {
    pub auto_squash_max_depth: usize,
}

/// Per-file read/write byte caps for `read_file` / `write_file` / `edit_file`.
///
/// Each bounds a single file payload. Both must stay below the transport frame
/// limit (the daemon wire `MAX_REQUEST_BYTES`, 16 MiB): file content travels
/// inside one request/response frame next to the JSON message, so values near
/// 16 MiB risk frame overflow once content is JSON-escaped.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileLimitsConfig {
    pub max_read_bytes: usize,
    pub max_write_bytes: usize,
}

/// Default `read_file` cap; the fallback when `daemon.files.max_read_bytes`
/// is not threaded through runtime config.
pub const MAX_READ_BYTES: usize = 16 * 1024 * 1024;
/// Default per-file `write_file` / `edit_file` cap; the fallback for
/// `daemon.files.max_write_bytes`. Kept below the 16 MiB request frame so a
/// single file payload fits one request.
pub const MAX_FILE_BYTES: usize = 8 * 1024 * 1024;

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
        require_absolute(
            &self.command_sessions.scratch_root,
            "daemon.command_sessions.scratch_root",
        )?;
        require_u64_at_least(
            self.command_sessions.default_yield_time_ms,
            1,
            "daemon.command_sessions.default_yield_time_ms",
        )?;
        require_u64_at_least(
            self.command_sessions.default_timeout_s,
            1,
            "daemon.command_sessions.default_timeout_s",
        )?;
        require_u64_at_least(
            self.command_sessions.quiet_ms,
            1,
            "daemon.command_sessions.quiet_ms",
        )?;
        require_u64_at_least(
            self.command_sessions.cancel_wait_ms,
            1,
            "daemon.command_sessions.cancel_wait_ms",
        )?;
        require_u64_at_least(
            self.command_sessions.output_drain_grace_ms,
            1,
            "daemon.command_sessions.output_drain_grace_ms",
        )?;
        require_u64_at_least(
            self.command_sessions.max_session_s,
            1,
            "daemon.command_sessions.max_session_s",
        )?;
        require_timestamp_timezone(
            &self.command_sessions.transcript_timestamp_timezone,
            "daemon.command_sessions.transcript_timestamp_timezone",
        )?;
        require_u64_at_least(
            self.isolated_sweeper.ttl_sweep_interval_ms,
            1,
            "daemon.isolated_sweeper.ttl_sweep_interval_ms",
        )?;
        require_absolute(&self.plugin.ppc_root, "daemon.plugin.ppc_root")?;
        require_u64_at_least(
            self.plugin.ppc_timeout_ms,
            1,
            "daemon.plugin.ppc_timeout_ms",
        )?;
        require_u64_at_least(
            self.plugin.service_probe_timeout_ms,
            1,
            "daemon.plugin.service_probe_timeout_ms",
        )?;
        require_usize_at_least(
            self.plugin.max_response_bytes,
            1,
            "daemon.plugin.max_response_bytes",
        )?;
        require_usize_at_least(
            self.layer_stack.auto_squash_max_depth,
            1,
            "daemon.layer_stack.auto_squash_max_depth",
        )?;
        require_usize_at_least(self.files.max_read_bytes, 1, "daemon.files.max_read_bytes")?;
        require_usize_at_least(
            self.files.max_write_bytes,
            1,
            "daemon.files.max_write_bytes",
        )?;
        Ok(())
    }
}

#[cfg(test)]
#[path = "../../tests/unit/configs/daemon.rs"]
mod tests;
