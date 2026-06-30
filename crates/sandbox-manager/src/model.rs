use std::fmt;
use std::path::PathBuf;

use crate::ManagerError;

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct SandboxId(String);

impl SandboxId {
    pub fn new(value: impl Into<String>) -> Result<Self, ManagerError> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err(ManagerError::InvalidSandboxId { value });
        }
        if !value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
        {
            return Err(ManagerError::InvalidSandboxId { value });
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for SandboxId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SharedBaseMount {
    pub source: PathBuf,
    pub target: PathBuf,
    pub root_hash: String,
    pub readonly: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SandboxRecord {
    pub id: SandboxId,
    pub workspace_root: PathBuf,
    pub state: SandboxState,
    pub daemon: Option<SandboxDaemonEndpoint>,
    pub daemon_http: Option<SandboxHttpEndpoint>,
    pub shared_base: Option<SharedBaseMount>,
}

impl SandboxRecord {
    #[must_use]
    pub fn new(id: SandboxId, workspace_root: PathBuf, state: SandboxState) -> Self {
        Self {
            id,
            workspace_root,
            state,
            daemon: None,
            daemon_http: None,
            shared_base: None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SandboxState {
    Creating,
    Ready,
    Stopping,
    Stopped,
    Failed,
}

impl SandboxState {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Creating => "creating",
            Self::Ready => "ready",
            Self::Stopping => "stopping",
            Self::Stopped => "stopped",
            Self::Failed => "failed",
        }
    }
}

impl fmt::Display for SandboxState {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SandboxDaemonEndpoint {
    pub host: String,
    pub port: u16,
    pub auth_token: String,
}

impl SandboxDaemonEndpoint {
    #[must_use]
    pub fn new(host: impl Into<String>, port: u16, auth_token: impl Into<String>) -> Self {
        Self {
            host: host.into(),
            port,
            auth_token: auth_token.into(),
        }
    }
}

/// The unauthenticated daemon HTTP endpoint published beside `daemon`. Carries
/// only a host and port; the HTTP surface has no auth token.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SandboxHttpEndpoint {
    pub host: String,
    pub port: u16,
}

impl SandboxHttpEndpoint {
    #[must_use]
    pub fn new(host: impl Into<String>, port: u16) -> Self {
        Self {
            host: host.into(),
            port,
        }
    }
}
