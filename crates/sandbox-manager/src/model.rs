use std::fmt;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::ManagerError;

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
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

impl TryFrom<String> for SandboxId {
    type Error = ManagerError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl From<SandboxId> for String {
    fn from(id: SandboxId) -> Self {
        id.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SharedBaseMount {
    pub source: PathBuf,
    pub target: PathBuf,
    pub root_hash: String,
    pub readonly: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SandboxRecord {
    pub id: SandboxId,
    pub workspace_root: PathBuf,
    pub state: SandboxState,
    #[serde(default)]
    pub activity_revision: u64,
    pub daemon: Option<SandboxDaemonEndpoint>,
    pub daemon_http: Option<SandboxHttpEndpoint>,
    pub shared_base: Option<SharedBaseMount>,
    #[serde(default)]
    pub resource_profile: Option<SandboxResourceProfile>,
}

impl SandboxRecord {
    #[must_use]
    pub fn new(id: SandboxId, workspace_root: PathBuf, state: SandboxState) -> Self {
        Self {
            id,
            workspace_root,
            state,
            activity_revision: 0,
            daemon: None,
            daemon_http: None,
            shared_base: None,
            resource_profile: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SandboxResourceProfile {
    pub name: String,
    pub nano_cpus: i64,
    pub memory_high_bytes: i64,
    pub memory_max_bytes: i64,
    pub pids_max: i64,
    #[serde(default)]
    pub workload_memory_high_bytes: i64,
    #[serde(default)]
    pub workload_memory_max_bytes: i64,
    #[serde(default)]
    pub workload_pids_max: i64,
    #[serde(default)]
    pub control_plane_pids_reserve: i64,
    pub daemon_runtime_profile: String,
    pub separate_workload_cgroup: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
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
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
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
