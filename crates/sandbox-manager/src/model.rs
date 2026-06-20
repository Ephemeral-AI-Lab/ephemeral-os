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
pub struct SandboxRecord {
    pub id: SandboxId,
    pub state: SandboxState,
    pub daemon: Option<SandboxDaemonEndpoint>,
}

impl SandboxRecord {
    #[must_use]
    pub const fn new(id: SandboxId, state: SandboxState) -> Self {
        Self {
            id,
            state,
            daemon: None,
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
    pub socket_path: PathBuf,
    pub auth_token: Option<String>,
}

impl SandboxDaemonEndpoint {
    #[must_use]
    pub fn new(socket_path: impl Into<PathBuf>, auth_token: Option<String>) -> Self {
        Self {
            socket_path: socket_path.into(),
            auth_token,
        }
    }
}
