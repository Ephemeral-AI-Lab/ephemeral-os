use std::sync::Arc;

use crate::command::CommandOperationService;
use crate::workspace_crate::{profile::WorkspaceModeManager, WorkspaceRuntimeService};
use crate::workspace_session::WorkspaceSessionService;

#[derive(Clone)]
pub struct SandboxRuntimeOperations {
    pub command: Arc<CommandOperationService>,
}

impl SandboxRuntimeOperations {
    #[must_use]
    pub fn new(command: Arc<CommandOperationService>) -> Self {
        Self { command }
    }

    #[must_use]
    pub fn from_config(config: SandboxRuntimeConfig) -> Self {
        let workspace_runtime = Arc::new(WorkspaceRuntimeService::new(WorkspaceModeManager::new(
            config
                .workspace
                .workspace_root
                .to_string_lossy()
                .into_owned(),
            config.workspace.caps.into(),
            config.workspace.scratch_root,
        )));
        let workspace_session = Arc::new(WorkspaceSessionService::new(workspace_runtime));
        let command = Arc::new(CommandOperationService::new(
            workspace_session,
            config.command.into(),
        ));
        Self::new(command)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SandboxRuntimeConfig {
    pub workspace: WorkspaceRuntimeConfig,
    pub command: CommandRuntimeConfig,
}

#[derive(Debug, Clone, PartialEq)]
pub struct WorkspaceRuntimeConfig {
    pub workspace_root: std::path::PathBuf,
    pub scratch_root: std::path::PathBuf,
    pub caps: WorkspaceResourceCaps,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommandRuntimeConfig {
    pub scratch_root: std::path::PathBuf,
}

#[derive(Debug, Clone, PartialEq)]
pub struct WorkspaceResourceCaps {
    pub ttl_s: f64,
    pub total_cap: u32,
    pub upperdir_bytes: u64,
    pub memavail_fraction: f64,
    pub setup_timeout_s: f64,
    pub exit_grace_s: f64,
    pub rfc1918_egress: Rfc1918Egress,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rfc1918Egress {
    Allow,
    Deny,
}

impl From<WorkspaceResourceCaps> for crate::workspace_crate::profile::ResourceCaps {
    fn from(caps: WorkspaceResourceCaps) -> Self {
        Self {
            ttl_s: caps.ttl_s,
            total_cap: caps.total_cap,
            upperdir_bytes: caps.upperdir_bytes,
            memavail_fraction: caps.memavail_fraction,
            setup_timeout_s: caps.setup_timeout_s,
            exit_grace_s: caps.exit_grace_s,
            rfc1918_egress: match caps.rfc1918_egress {
                Rfc1918Egress::Allow => crate::workspace_crate::profile::Rfc1918Egress::Allow,
                Rfc1918Egress::Deny => crate::workspace_crate::profile::Rfc1918Egress::Deny,
            },
        }
    }
}

impl From<CommandRuntimeConfig> for ::sandbox_runtime_command::CommandConfig {
    fn from(config: CommandRuntimeConfig) -> Self {
        Self {
            scratch_root: config.scratch_root,
        }
    }
}
