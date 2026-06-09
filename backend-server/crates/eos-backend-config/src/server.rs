//! `ServerConfig`: the top-level backend deployment config.

use std::net::SocketAddr;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::loader::ConfigError;
use crate::obs::ObsConfig;
use crate::sandbox::SandboxConfig;

/// Backend deployment config, deserialized from `backend.yml < local.yml`.
///
/// It owns only backend deployment and sandbox lifecycle defaults.
/// `ProvidersConfig` and `WorkflowConfig` are deliberately absent: agent-core's
/// owner-local loaders validate those from `agent_core.config_dir`.
/// `deny_unknown_fields` makes a stray `providers:` / `workflow:` section a hard
/// error rather than a silently ignored key (AC11).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct ServerConfig {
    /// HTTP listen address.
    pub bind: SocketAddr,
    /// Path to the backend `backend.db` file.
    pub backend_db_path: PathBuf,
    /// Where and how the backend composition root sources agent-core config.
    pub agent_core: AgentCoreConfigSource,
    /// Sandbox lifecycle and provisioning defaults.
    pub sandbox: SandboxConfig,
    /// Observability persistence defaults.
    pub obs: ObsConfig,
}

impl ServerConfig {
    /// Validate nested fields. Call after deserializing.
    ///
    /// # Errors
    /// Propagates [`ConfigError::OutOfRange`] from the sandbox/obs sections and
    /// [`ConfigError::Empty`] from the agent-core source.
    pub fn validate(&self) -> Result<(), ConfigError> {
        self.agent_core.validate()?;
        self.sandbox.validate()?;
        self.obs.validate()?;
        Ok(())
    }
}

impl AgentCoreConfigSource {
    /// Reject empty required-identity fields, which would otherwise fail much
    /// later at DB-connect time with an opaque error.
    ///
    /// # Errors
    /// [`ConfigError::Empty`] when `config_dir`, `database_url`, or
    /// `message_records_root` is empty.
    pub fn validate(&self) -> Result<(), ConfigError> {
        if self.config_dir.as_os_str().is_empty() {
            return Err(ConfigError::Empty {
                field: "agent_core.config_dir",
            });
        }
        if self.database_url.trim().is_empty() {
            return Err(ConfigError::Empty {
                field: "agent_core.database_url",
            });
        }
        if self.message_records_root.as_os_str().is_empty() {
            return Err(ConfigError::Empty {
                field: "agent_core.message_records_root",
            });
        }
        Ok(())
    }
}

/// How the backend composition root locates agent-core's own config and DB.
///
/// The backend supplies a deterministic config directory and database path; it
/// does not embed agent-core's provider/workflow schema. agent-core loads those
/// from `config_dir/prd.yml < local.yml` itself.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct AgentCoreConfigSource {
    /// Directory holding agent-core's `prd.yml` (and optional `local.yml`).
    pub config_dir: PathBuf,
    /// Deployable agent-core database url the backend supplies.
    pub database_url: String,
    /// Root directory for agent-core-owned node message records.
    pub message_records_root: PathBuf,
}
