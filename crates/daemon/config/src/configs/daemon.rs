//! Typed schema for the daemon section of `eos-sandbox/config/prd.yml`.
//!
//! The `eosd` binary loads this section from the merged runtime YAML and injects
//! it into daemon-owned subsystems during server startup.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::configs::validate::{
    require_absolute, require_f64_gt, require_timestamp_timezone, require_u64_at_least,
    require_usize_at_least, require_usize_at_most, ConfigFieldError,
};

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DaemonConfig {
    pub server: DaemonServerConfig,
    pub inflight: InflightConfig,
    pub commands: CommandConfig,
    pub idle_workspace_eviction: IdleWorkspaceEvictionConfig,
    pub plugin: PluginRuntimeConfig,
    pub layer_stack: LayerStackConfig,
    pub files: FileLimitsConfig,
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

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PluginRuntimeConfig {
    pub max_response_bytes: usize,
    pub enabled_plugins: Vec<String>,
    pub pyright_lsp: PyrightLspConfig,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PyrightLspConfig {
    pub node_path: PathBuf,
    pub pyright_langserver_path: PathBuf,
    pub workspace_root: PathBuf,
    pub analysis_timeout_ms: u64,
    pub refresh_timeout_ms: u64,
}

impl Default for PluginRuntimeConfig {
    /// Production fallbacks used when no `daemon.plugin` section is injected
    /// (matches `eos-sandbox/config/prd.yml`).
    fn default() -> Self {
        Self {
            max_response_bytes: 8 * 1024 * 1024,
            enabled_plugins: Vec::new(),
            pyright_lsp: PyrightLspConfig::default(),
        }
    }
}

impl Default for PyrightLspConfig {
    fn default() -> Self {
        Self {
            node_path: PathBuf::from("/usr/local/bin/node"),
            pyright_langserver_path: PathBuf::from(
                "/usr/local/lib/node_modules/pyright/langserver.index.js",
            ),
            workspace_root: PathBuf::from("/eos/runtime/plugins/pyright_lsp/workspace"),
            analysis_timeout_ms: 10_000,
            refresh_timeout_ms: 5_000,
        }
    }
}

pub const PYRIGHT_LSP_PLUGIN_ID: &str = "pyright_lsp";

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
/// is not threaded through runtime config. Kept below the 16 MiB response frame.
pub const MAX_READ_BYTES: usize = 8 * 1024 * 1024;
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
        require_absolute(&self.commands.scratch_root, "daemon.commands.scratch_root")?;
        reject_dangerous_command_scratch_root(
            &self.commands.scratch_root,
            &self.plugin.pyright_lsp.workspace_root,
        )?;
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
            self.plugin.max_response_bytes,
            1,
            "daemon.plugin.max_response_bytes",
        )?;
        validate_enabled_plugins(&self.plugin.enabled_plugins)?;
        require_absolute(
            &self.plugin.pyright_lsp.node_path,
            "daemon.plugin.pyright_lsp.node_path",
        )?;
        require_absolute(
            &self.plugin.pyright_lsp.pyright_langserver_path,
            "daemon.plugin.pyright_lsp.pyright_langserver_path",
        )?;
        require_absolute(
            &self.plugin.pyright_lsp.workspace_root,
            "daemon.plugin.pyright_lsp.workspace_root",
        )?;
        require_u64_at_least(
            self.plugin.pyright_lsp.analysis_timeout_ms,
            1,
            "daemon.plugin.pyright_lsp.analysis_timeout_ms",
        )?;
        require_u64_at_least(
            self.plugin.pyright_lsp.refresh_timeout_ms,
            1,
            "daemon.plugin.pyright_lsp.refresh_timeout_ms",
        )?;
        require_usize_at_least(
            self.layer_stack.auto_squash_max_depth,
            1,
            "daemon.layer_stack.auto_squash_max_depth",
        )?;
        require_usize_at_least(self.files.max_read_bytes, 1, "daemon.files.max_read_bytes")?;
        require_usize_at_most(
            self.files.max_read_bytes,
            MAX_READ_BYTES,
            "daemon.files.max_read_bytes",
        )?;
        require_usize_at_least(
            self.files.max_write_bytes,
            1,
            "daemon.files.max_write_bytes",
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

impl PluginRuntimeConfig {
    #[must_use]
    pub fn pyright_lsp_enabled(&self) -> bool {
        self.enabled_plugins
            .iter()
            .any(|plugin| plugin == PYRIGHT_LSP_PLUGIN_ID)
    }
}

fn validate_enabled_plugins(enabled: &[String]) -> Result<(), ConfigFieldError> {
    let mut seen = BTreeSet::new();
    for plugin in enabled {
        match plugin.as_str() {
            PYRIGHT_LSP_PLUGIN_ID => {}
            _ => {
                return Err(ConfigFieldError::new(
                    "daemon.plugin.enabled_plugins",
                    format!("unknown plugin id {plugin:?}"),
                ));
            }
        }
        if !seen.insert(plugin.as_str()) {
            return Err(ConfigFieldError::new(
                "daemon.plugin.enabled_plugins",
                format!("duplicate plugin id {plugin:?}"),
            ));
        }
    }
    Ok(())
}

fn reject_dangerous_command_scratch_root(
    scratch_root: &Path,
    plugin_workspace_root: &Path,
) -> Result<(), ConfigFieldError> {
    if is_filesystem_root(scratch_root) {
        return Err(ConfigFieldError::new(
            "daemon.commands.scratch_root",
            "must not be the filesystem root",
        ));
    }
    if paths_match_or_resolve_equal(scratch_root, plugin_workspace_root) {
        return Err(ConfigFieldError::new(
            "daemon.commands.scratch_root",
            "must not resolve to daemon.plugin.pyright_lsp.workspace_root",
        ));
    }
    Ok(())
}

fn is_filesystem_root(path: &Path) -> bool {
    path.parent().is_none()
        || path
            .canonicalize()
            .ok()
            .is_some_and(|canonical| canonical.parent().is_none())
}

fn paths_match_or_resolve_equal(left: &Path, right: &Path) -> bool {
    left == right
        || match (left.canonicalize(), right.canonicalize()) {
            (Ok(left), Ok(right)) => left == right,
            _ => false,
        }
}

#[cfg(test)]
#[path = "../../tests/unit/configs/daemon.rs"]
mod tests;
