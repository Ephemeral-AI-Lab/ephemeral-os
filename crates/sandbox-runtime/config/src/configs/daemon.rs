//! Typed schema for the daemon section of `eos-sandbox/config/prd.yml`.
//!
//! The `sandbox-daemon` binary loads this section from the merged runtime YAML
//! and injects it into daemon-owned subsystems during server startup.

use std::path::PathBuf;

use serde::Deserialize;

use crate::configs::validate::{
    require_absolute, require_u64_at_least, require_usize_at_least, ConfigFieldError,
};

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DaemonConfig {
    pub server: DaemonServerConfig,
    pub commands: CommandConfig,
    pub idle_workspace_eviction: IdleWorkspaceEvictionConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CommandConfig {
    pub scratch_root: PathBuf,
}

impl Default for CommandConfig {
    fn default() -> Self {
        Self {
            scratch_root: PathBuf::from("/eos/scratch/commands"),
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
pub struct IdleWorkspaceEvictionConfig {
    pub interval_ms: u64,
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
        require_absolute(&self.commands.scratch_root, "daemon.commands.scratch_root")?;
        reject_dangerous_command_scratch_root(&self.commands.scratch_root)?;
        require_u64_at_least(
            self.idle_workspace_eviction.interval_ms,
            1,
            "daemon.idle_workspace_eviction.interval_ms",
        )?;
        Ok(())
    }
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
