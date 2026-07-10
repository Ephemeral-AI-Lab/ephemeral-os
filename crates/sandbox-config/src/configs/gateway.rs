//! Typed schema for the optional `gateway` section of the sandbox config,
//! doubling as the gateway server's runtime config.

use std::path::PathBuf;

use serde::Deserialize;

use crate::configs::validate::{
    require_non_empty, require_socket_addr, require_usize_at_least, ConfigFieldError,
};

pub const DEFAULT_GATEWAY_SOCKET: &str = "127.0.0.1:7878";
pub const DEFAULT_GATEWAY_PID: &str = "/tmp/eos-gateway.pid";
pub const DEFAULT_MAX_CONCURRENT_CONNECTIONS: usize = 256;
pub const SANDBOX_GATEWAY_SOCKET_ENV: &str = "SANDBOX_GATEWAY_SOCKET";
pub const SANDBOX_GATEWAY_AUTH_TOKEN_ENV: &str = "SANDBOX_GATEWAY_AUTH_TOKEN";

/// Gateway server config. The YAML `gateway` section feeds `bind_addr`,
/// `pid_path`, and `max_concurrent_connections`; the auth token is runtime
/// state resolved from flag/env only and never deserializes from YAML.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct GatewayConfig {
    pub bind_addr: String,
    pub pid_path: PathBuf,
    pub max_concurrent_connections: usize,
    #[serde(skip)]
    pub auth_token: Option<String>,
}

impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            bind_addr: DEFAULT_GATEWAY_SOCKET.to_owned(),
            pid_path: PathBuf::from(DEFAULT_GATEWAY_PID),
            max_concurrent_connections: DEFAULT_MAX_CONCURRENT_CONNECTIONS,
            auth_token: None,
        }
    }
}

impl GatewayConfig {
    #[must_use]
    pub fn new(
        bind_addr: impl Into<String>,
        pid_path: impl Into<PathBuf>,
        max_concurrent_connections: usize,
        auth_token: Option<String>,
    ) -> Self {
        Self {
            bind_addr: bind_addr.into(),
            pid_path: pid_path.into(),
            max_concurrent_connections,
            auth_token,
        }
    }

    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when a field violates gateway policy.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        require_socket_addr(&self.bind_addr, "gateway.bind_addr")?;
        require_non_empty(&self.pid_path.to_string_lossy(), "gateway.pid_path")?;
        require_usize_at_least(
            self.max_concurrent_connections,
            1,
            "gateway.max_concurrent_connections",
        )
    }
}
