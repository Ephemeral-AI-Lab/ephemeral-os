//! Typed schema for the daemon section of `eos-sandbox/config/prd.yml`.
//!
//! The `sandbox-daemon` binary loads this section from the merged sandbox YAML
//! and injects it into daemon-owned subsystems during server startup. The
//! request limits map into the `sandbox-protocol` `ProtocolLimits` value type
//! at startup; the protocol crate never imports this one.

use std::path::PathBuf;

use serde::Deserialize;

use crate::configs::validate::{
    require_absolute, require_f64_gt, require_usize_at_least, ConfigFieldError,
};

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DaemonConfig {
    pub server: DaemonServerConfig,
    #[serde(default)]
    pub http: DaemonHttpConfig,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DaemonServerConfig {
    pub socket_path: PathBuf,
    pub pid_path: PathBuf,
    pub max_worker_threads: usize,
    /// RPC connection-permit count (both listeners share one semaphore).
    #[serde(default = "default_max_concurrent_connections")]
    pub max_concurrent_connections: usize,
    /// Request envelope byte cap, enforced on the RPC read path and the HTTP
    /// API body.
    #[serde(default = "default_max_request_bytes")]
    pub max_request_bytes: usize,
    /// Deadline for reading one request line off an accepted connection.
    #[serde(default = "default_request_read_timeout_s")]
    pub request_read_timeout_s: f64,
}

/// Daemon HTTP surface tuning (`daemon.http`).
#[derive(Debug, Clone, Copy, Default, PartialEq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct DaemonHttpConfig {
    pub forward: DaemonHttpForwardConfig,
}

/// `/forward` reverse-proxy deadlines (`daemon.http.forward`).
#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct DaemonHttpForwardConfig {
    /// TCP connect deadline toward the forward target.
    pub connect_timeout_s: f64,
    /// Upstream response deadline once connected.
    pub response_timeout_s: f64,
}

impl Default for DaemonHttpForwardConfig {
    fn default() -> Self {
        Self {
            connect_timeout_s: 10.0,
            response_timeout_s: 30.0,
        }
    }
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
        require_usize_at_least(
            self.server.max_concurrent_connections,
            1,
            "daemon.server.max_concurrent_connections",
        )?;
        require_usize_at_least(
            self.server.max_request_bytes,
            65536,
            "daemon.server.max_request_bytes",
        )?;
        require_f64_gt(
            self.server.request_read_timeout_s,
            0.0,
            "daemon.server.request_read_timeout_s",
        )?;
        require_f64_gt(
            self.http.forward.connect_timeout_s,
            0.0,
            "daemon.http.forward.connect_timeout_s",
        )?;
        require_f64_gt(
            self.http.forward.response_timeout_s,
            0.0,
            "daemon.http.forward.response_timeout_s",
        )?;
        Ok(())
    }
}

fn default_max_concurrent_connections() -> usize {
    256
}

fn default_max_request_bytes() -> usize {
    16 * 1024 * 1024
}

fn default_request_read_timeout_s() -> f64 {
    30.0
}
